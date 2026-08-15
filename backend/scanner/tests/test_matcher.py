import pytest

from scanner.constants import (
    AUTO_ACCEPT_MARGIN_THRESHOLD,
    AUTO_ACCEPT_SCORE_THRESHOLD,
    UNREADABLE_AUTHOR_CONFIDENCE_CAP,
)
from scanner.services import matcher

# ---------- normalization ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("The Hobbit", "hobbit"),
        ("Dune", "dune"),
        ("Foundation and Empire", "foundation and empire"),
        ("Harry Potter and the Philosopher's Stone", "harry potter and the philosophers stone"),
        ("  Snow   Crash  ", "snow crash"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_text(raw, expected):
    assert matcher.normalize_text(raw) == expected


def test_normalize_text_strips_accents():
    assert matcher.normalize_text("Cien años de soledad") == "cien anos de soledad"


# ---------- author normalization: each variant form (ambiguity #6) ----------


def test_author_lastname_firstname_matches_firstname_lastname():
    a = matcher.normalize_author("Orwell, George")
    b = matcher.normalize_author("George Orwell")
    assert a.surname == b.surname == "orwell"
    assert a.initials == b.initials == ("g",)


def test_author_glued_initials_match_spaced_lastname_first_initials():
    a = matcher.normalize_author("J.K. Rowling")
    b = matcher.normalize_author("Rowling, J. K.")
    assert a.surname == b.surname == "rowling"
    assert a.initials == b.initials == ("j", "k")


def test_author_accented_matches_unaccented():
    a = matcher.normalize_author("Gabriel García Márquez")
    b = matcher.normalize_author("Gabriel Garcia Marquez")
    assert a.surname == b.surname == "marquez"
    assert a.initials == b.initials


def test_author_score_full_marks_for_matching_forms():
    assert matcher.author_score("J.K. Rowling", "Rowling, J. K.") == pytest.approx(1.0)
    assert matcher.author_score("George Orwell", "Orwell, George") == pytest.approx(1.0)
    assert matcher.author_score("Gabriel García Márquez", "Gabriel Garcia Marquez") == pytest.approx(1.0)


def test_author_score_different_surname_is_zero():
    assert matcher.author_score("Isaac Asimov", "Frank Herbert") == 0.0


def test_author_score_unparseable_is_none():
    assert matcher.author_score(None, "George Orwell") is None
    assert matcher.author_score("", "George Orwell") is None


# ---------- alternate-title hit (ambiguity #2) ----------


def test_alt_title_hit_uk_us_edition():
    score = matcher.title_score(
        "Harry Potter and the Sorcerer's Stone",
        "Harry Potter and the Philosopher's Stone",
        ["Harry Potter and the Sorcerer's Stone"],
    )
    assert score > 0.95


def test_alt_title_hit_northern_lights_golden_compass():
    score = matcher.title_score("The Golden Compass", "Northern Lights", ["The Golden Compass"])
    assert score > 0.95


# ---------- the substring trap (ambiguity #5) ----------


def _dune_catalog():
    return [
        {"title": "Dune", "author": "Frank Herbert", "alt_titles": []},
        {"title": "Dune Messiah", "author": "Frank Herbert", "alt_titles": []},
    ]


def test_substring_trap_ranks_the_exact_title_first():
    result = matcher.match_book("Dune", "Frank Herbert", _dune_catalog())
    assert result.candidates[0].book["title"] == "Dune"
    result = matcher.match_book("Dune Messiah", "Frank Herbert", _dune_catalog())
    assert result.candidates[0].book["title"] == "Dune Messiah"


def test_substring_trap_margin_is_too_small_to_auto_accept():
    # "Dune" and "Dune Messiah" are fuzzy-similar enough (one contains the
    # other) that the margin between them stays below the auto-accept
    # threshold, even though the top pick is correct -- this is the "Dune
    # vs. Dune Messiah (small margin -> review)" case from CONTEXT.md.
    result = matcher.match_book("Dune", "Frank Herbert", _dune_catalog())
    assert result.margin < AUTO_ACCEPT_MARGIN_THRESHOLD
    assert result.status == matcher.STATUS_NEEDS_REVIEW


def test_substring_trap_foundation_family_ranks_correctly():
    catalog = [
        {"title": "Foundation", "author": "Isaac Asimov", "alt_titles": []},
        {"title": "Foundation and Empire", "author": "Isaac Asimov", "alt_titles": []},
        {"title": "Second Foundation", "author": "Isaac Asimov", "alt_titles": []},
    ]
    result = matcher.match_book("Foundation and Empire", "Isaac Asimov", catalog)
    assert result.candidates[0].book["title"] == "Foundation and Empire"


# ---------- margin demotion on a shared-title pair (ambiguity #3) ----------


def _shared_title_catalog():
    return [
        {"title": "The Power", "author": "Naomi Alderman", "alt_titles": []},
        {"title": "The Power", "author": "Rhonda Byrne", "alt_titles": []},
    ]


def test_shared_title_with_author_read_resolves_cleanly():
    result = matcher.match_book("The Power", "Naomi Alderman", _shared_title_catalog())
    assert result.status == matcher.STATUS_AUTO_ADDED
    assert result.candidates[0].book["author"] == "Naomi Alderman"
    assert result.margin >= AUTO_ACCEPT_MARGIN_THRESHOLD


def test_shared_title_without_author_demotes_to_review():
    # Same title, author unreadable -> the two candidates are indistinguishable.
    result = matcher.match_book("The Power", None, _shared_title_catalog())
    assert result.status == matcher.STATUS_NEEDS_REVIEW
    assert result.margin == pytest.approx(0.0)


def test_two_editions_tie_demotes_to_review():
    # Ambiguity #1: identical title+author, two catalog rows -> tie, review.
    catalog = [
        {"title": "Fahrenheit 451", "author": "Ray Bradbury", "alt_titles": []},
        {"title": "Fahrenheit 451", "author": "Ray Bradbury", "alt_titles": []},
    ]
    result = matcher.match_book("Fahrenheit 451", "Ray Bradbury", catalog)
    assert result.status == matcher.STATUS_NEEDS_REVIEW
    assert result.margin == pytest.approx(0.0)
    assert result.confidence >= AUTO_ACCEPT_SCORE_THRESHOLD  # score alone would auto-accept


# ---------- below-threshold -> unmatched ----------


def test_unrelated_title_is_unmatched():
    catalog = [{"title": "Dune", "author": "Frank Herbert", "alt_titles": []}]
    result = matcher.match_book("Completely Different Title Xyz", "Nobody Real", catalog)
    assert result.status == matcher.STATUS_UNMATCHED


def test_empty_read_title_is_unmatched():
    catalog = [{"title": "Dune", "author": "Frank Herbert", "alt_titles": []}]
    result = matcher.match_book("", None, catalog)
    assert result.status == matcher.STATUS_UNMATCHED
    assert result.candidates == []


# ---------- unreadable author caps confidence ----------


def test_unreadable_author_caps_confidence_and_blocks_auto_accept():
    catalog = [{"title": "Dune", "author": "Frank Herbert", "alt_titles": []}]
    result = matcher.match_book("Dune", None, catalog)
    assert result.confidence == pytest.approx(UNREADABLE_AUTHOR_CONFIDENCE_CAP)
    assert UNREADABLE_AUTHOR_CONFIDENCE_CAP < AUTO_ACCEPT_SCORE_THRESHOLD
    assert result.status != matcher.STATUS_AUTO_ADDED
