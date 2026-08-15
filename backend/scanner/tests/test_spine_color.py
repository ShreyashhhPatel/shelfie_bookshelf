"""
Spine colour: reading it off the crop, folding it into a closed vocabulary,
and letting it break a tie the text scores could not.

The invariant that matters most is the last one: colour re-orders a review
card, it never promotes anything to auto-accept. It is a weak signal read
under unknown lighting, and a wrong auto-add reaches the library with no
human step in the way.
"""

import pytest

from scanner.constants import SPINE_COLOR_VOCABULARY
from scanner.services import matcher, vlm_read
from scanner.services.matcher import Candidate, MatchResult, apply_color_tiebreak
from scanner.services.vlm_read import normalize_spine_color


# ---------- vocabulary folding ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("yellow", "yellow"),
        ("Yellow", "yellow"),
        ("  PURPLE  ", "purple"),
        ("grey", "gray"),  # spelling
        ("navy", "blue"),  # synonym
        ("burgundy", "red"),
        ("lilac", "purple"),
        ("multicolored", "multicolour"),
        ("dark navy blue", "blue"),  # head noun is last
        ("pale yellow", "yellow"),
        ("", None),
        (None, None),
        (123, None),
        ("chartreuse", None),  # unrecognised -> dropped, never guessed
        ("wooden", None),
    ],
)
def test_normalize_spine_color(raw, expected):
    assert normalize_spine_color(raw) == expected


def test_every_vocabulary_word_survives_normalization():
    for word in SPINE_COLOR_VOCABULARY:
        assert normalize_spine_color(word) == word


# ---------- the prompt actually asks for it ----------


def test_batch_prompt_constrains_colour_to_the_vocabulary():
    prompt = vlm_read._build_batch_prompt(3)
    assert "spine_color" in prompt
    for word in SPINE_COLOR_VOCABULARY:
        assert word in prompt


def test_full_image_prompt_asks_for_colour_too():
    assert "spine_color" in vlm_read._FULL_IMAGE_PROMPT


# ---------- colour survives the normalizer into the read ----------


def test_colour_is_kept_even_when_the_text_is_unreadable():
    """Colour comes off the crop, not the lettering, so a blurry spine keeps it."""
    parsed = [{"index": 1, "title": None, "readable": False, "spine_color": "Navy"}]
    result = vlm_read._normalize_batch_results(parsed, 1)[0]

    assert result["readable"] is False
    assert result["spine_color"] == "blue"


def test_unrecognised_colour_becomes_none_rather_than_garbage():
    parsed = [{"index": 1, "title": "Dune", "readable": True, "spine_color": "sandy dune tone"}]
    assert vlm_read._normalize_batch_results(parsed, 1)[0]["spine_color"] is None


def test_missing_colour_key_is_tolerated():
    parsed = [{"index": 1, "title": "Dune", "readable": True}]
    assert vlm_read._normalize_batch_results(parsed, 1)[0]["spine_color"] is None


# ---------- the tie-break ----------


def _tied_result(margin=0.0, colors=("black", "purple")):
    candidates = [
        Candidate(book={"id": i, "title": "The Power", "author": "A", "spine_color": c}, score=1.0,
                  title_score=1.0, author_score=1.0)
        for i, c in enumerate(colors)
    ]
    return MatchResult(status=matcher.STATUS_NEEDS_REVIEW, confidence=1.0, margin=margin, candidates=candidates)


def test_colour_promotes_the_matching_candidate_to_the_top():
    result = apply_color_tiebreak(_tied_result(), "purple")
    assert result.candidates[0].book["spine_color"] == "purple"


def test_tiebreak_never_changes_the_decision():
    """The whole safety argument: presentation moves, status does not."""
    before = _tied_result()
    after = apply_color_tiebreak(before, "purple")

    assert after.status == before.status == matcher.STATUS_NEEDS_REVIEW
    assert after.confidence == before.confidence
    assert after.margin == before.margin
    assert len(after.candidates) == len(before.candidates)


def test_a_clear_winner_on_text_is_left_alone():
    """Above the margin the text evidence is decisive; colour must not reorder it."""
    result = _tied_result(margin=0.4)
    assert apply_color_tiebreak(result, "purple").candidates[0].book["spine_color"] == "black"


def test_no_read_colour_is_a_no_op():
    result = _tied_result()
    assert apply_color_tiebreak(result, None).candidates[0].book["spine_color"] == "black"


def test_colour_matching_two_candidates_resolves_nothing():
    """Ambiguous colour leaves the order alone rather than picking arbitrarily."""
    result = _tied_result(colors=("purple", "purple"))
    assert apply_color_tiebreak(result, "purple").candidates[0].book["id"] == 0


def test_catalog_without_colour_is_a_no_op():
    result = _tied_result(colors=("", ""))
    assert apply_color_tiebreak(result, "purple").candidates[0].book["id"] == 0


def test_tiebreak_handles_an_empty_candidate_list():
    empty = MatchResult(status=matcher.STATUS_UNMATCHED, confidence=None, margin=None, candidates=[])
    assert apply_color_tiebreak(empty, "purple").candidates == []


# ---------- not_a_book: pillars, cartons, furniture ----------


def test_not_a_book_is_separated_from_merely_unreadable():
    """
    The distinction that keeps the review queue useful. Both crops are
    unreadable, but only one is worth a person's time.
    """
    parsed = [
        {"index": 1, "title": None, "readable": False, "not_a_book": True},   # a pillar
        {"index": 2, "title": None, "readable": False, "not_a_book": False},  # a blurry spine
    ]
    results = vlm_read._normalize_batch_results(parsed, 2)

    assert results[0]["status"] == "not_a_book"
    assert results[1]["status"] == "unreadable"


def test_a_readable_spine_is_never_marked_not_a_book():
    """A model contradicting itself must not lose a book we could read."""
    parsed = [{"index": 1, "title": "Dune", "readable": True, "not_a_book": True}]
    assert vlm_read._normalize_batch_results(parsed, 1)[0]["status"] == "ok"


def test_missing_not_a_book_key_defaults_to_false():
    parsed = [{"index": 1, "title": None, "readable": False}]
    result = vlm_read._normalize_batch_results(parsed, 1)[0]
    assert result["not_a_book"] is False
    assert result["status"] == "unreadable"


def test_prompt_explains_the_not_a_book_distinction():
    prompt = vlm_read._build_batch_prompt(2)
    assert "not_a_book" in prompt
    assert "pillar" in prompt
