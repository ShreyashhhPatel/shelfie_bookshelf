"""One test per ambiguity planted in catalog/catalog.csv.

These run against the real CSV rather than a hand-built fixture, so a test
failing means the matcher disagrees with the actual catalog the app ships,
not with a convenient copy of it. No database, no network, no model.

Read catalog/AMBIGUITIES.md alongside this file; the numbered cases there and
the test classes here are the same six things.
"""

import csv

import pytest

from scanner.constants import (
    AUTO_ACCEPT_MARGIN,
    AUTO_ACCEPT_SCORE,
    BACKEND_DIR,
    NO_AUTHOR_SCORE_CAP,
    split_multi,
)
from scanner.services.matcher import CatalogEntry, match


@pytest.fixture(scope='module')
def catalog() -> list[CatalogEntry]:
    path = BACKEND_DIR / 'catalog' / 'catalog.csv'
    with path.open(newline='', encoding='utf-8') as handle:
        return [
            CatalogEntry(
                id=index,
                title=row['title'],
                author=row['author'],
                alt_titles=tuple(split_multi(row['alt_titles'])),
                is_omnibus=row['is_omnibus'].strip().lower() == 'true',
            )
            for index, row in enumerate(csv.DictReader(handle), start=1)
        ]


def titles(result, count=2):
    return [candidate.entry.title for candidate in result.candidates[:count]]


class TestCase1PrefixCollision:
    """Dune / Dune Messiah / Children of Dune / God Emperor / Heretics."""

    def test_exact_title_beats_its_own_sequels(self, catalog):
        result = match('Dune', 'Frank Herbert', catalog)

        assert result.best.entry.title == 'Dune'
        assert result.should_auto_accept

    def test_sequel_does_not_collapse_into_the_parent(self, catalog):
        result = match('Dune Messiah', 'Frank Herbert', catalog)

        assert result.best.entry.title == 'Dune Messiah'
        assert result.should_auto_accept

    def test_scoring_is_symmetric(self, catalog):
        """The heart of case 1.

        A partial-ratio scorer treats "Dune" as a perfect hit against every
        title containing it. Token-sort keeps the comparison symmetric, so the
        sequels' extra words cost them.
        """
        result = match('Dune', 'Frank Herbert', catalog)
        by_title = {c.entry.title: c.title_score for c in result.candidates}

        assert by_title['Dune'] == 1.0
        assert by_title.get('Dune Messiah', 0.0) < 0.8


class TestCase2AlternateTitles:
    """Northern Lights is shelved as The Golden Compass in the US."""

    def test_alt_title_matches_at_full_strength(self, catalog):
        result = match('The Golden Compass', 'Philip Pullman', catalog)

        assert result.best.entry.title == 'Northern Lights'
        assert result.best.title_score == 1.0
        assert result.should_auto_accept

    def test_result_reports_which_title_matched(self, catalog):
        """So the UI can show the name on the spine the reader is holding."""
        result = match('The Golden Compass', 'Philip Pullman', catalog)

        assert result.best.matched_title == 'The Golden Compass'

    def test_canonical_title_still_matches(self, catalog):
        result = match('Northern Lights', 'Philip Pullman', catalog)

        assert result.best.entry.title == 'Northern Lights'
        assert result.best.matched_title == 'Northern Lights'


class TestCase3OmnibusContainment:
    """One spine, several works. Containment is not a match key."""

    def test_contained_volume_matches_the_standalone_row(self, catalog):
        result = match('The Two Towers', 'J.R.R. Tolkien', catalog)

        assert result.best.entry.title == 'The Two Towers'
        assert not result.best.entry.is_omnibus

    def test_omnibus_matches_only_its_own_name(self, catalog):
        result = match('The Lord of the Rings', 'J.R.R. Tolkien', catalog)

        assert result.best.entry.title == 'The Lord of the Rings'
        assert result.best.entry.is_omnibus

    def test_alt_title_row_inside_an_omnibus_is_unaffected(self, catalog):
        """Northern Lights is both a case 2 row and inside His Dark Materials."""
        result = match('The Subtle Knife', 'Philip Pullman', catalog)

        assert result.best.entry.title == 'The Subtle Knife'
        assert not result.best.entry.is_omnibus


class TestCase4SameTitleDifferentAuthor:
    """The Idiot: Dostoevsky 1869, Batuman 2017."""

    def test_author_resolves_dostoevsky(self, catalog):
        result = match('The Idiot', 'Fyodor Dostoevsky', catalog)

        assert result.best.entry.author == 'Fyodor Dostoevsky'
        assert result.should_auto_accept

    def test_author_resolves_batuman(self, catalog):
        result = match('The Idiot', 'Elif Batuman', catalog)

        assert result.best.entry.author == 'Elif Batuman'
        assert result.should_auto_accept

    def test_surname_alone_is_enough_to_resolve(self, catalog):
        """Spines frequently print only a surname."""
        result = match('The Idiot', 'Batuman', catalog)

        assert result.best.entry.author == 'Elif Batuman'

    def test_without_an_author_it_refuses_to_choose(self, catalog):
        result = match('The Idiot', '', catalog)

        assert not result.should_auto_accept
        assert result.margin == pytest.approx(0.0)
        # Both are offered so the reader can pick.
        assert {c.entry.author for c in result.candidates[:2]} == {
            'Fyodor Dostoevsky',
            'Elif Batuman',
        }


class TestCase5SameAuthorDifferentPeople:
    """David Mitchell the novelist and David Mitchell the comedian."""

    def test_title_selects_the_novel(self, catalog):
        result = match('Cloud Atlas', 'David Mitchell', catalog)

        assert result.best.entry.title == 'Cloud Atlas'

    def test_title_selects_the_memoir(self, catalog):
        result = match('Back Story', 'David Mitchell', catalog)

        assert result.best.entry.title == 'Back Story'

    def test_author_alone_cannot_select_anything(self, catalog):
        """An author with no title is not a match, however confident."""
        result = match('', 'David Mitchell', catalog)

        assert result.best is None

    def test_shared_surname_is_separated_by_initials(self, catalog):
        """Charlotte and Emily Bronte: same surname, same year, 1847."""
        charlotte = match('Jane Eyre', 'C. Bronte', catalog)
        emily = match('Wuthering Heights', 'E. Bronte', catalog)

        assert charlotte.best.entry.author == 'Charlotte Brontë'
        assert emily.best.entry.author == 'Emily Brontë'


class TestCase6SubtitleCollision:
    """Sapiens: A Brief History of Humankind / A Graphic History, Volume 1."""

    def test_full_title_resolves_cleanly(self, catalog):
        result = match(
            'Sapiens: A Brief History of Humankind', 'Yuval Noah Harari', catalog
        )

        assert result.best.entry.title == 'Sapiens: A Brief History of Humankind'
        assert result.should_auto_accept

    def test_main_title_alone_surfaces_both_and_decides_neither(self, catalog):
        """A crop that lost the subtitle -- the common case, it is set small."""
        result = match('Sapiens', 'Yuval Noah Harari', catalog)

        assert not result.should_auto_accept
        assert result.margin == pytest.approx(0.0)
        assert set(titles(result)) == {
            'Sapiens: A Brief History of Humankind',
            'Sapiens: A Graphic History, Volume 1',
        }

    def test_subtitles_are_not_stripped_during_normalization(self, catalog):
        """If they were, the two rows would be one and this would be 1."""
        result = match('Sapiens', 'Yuval Noah Harari', catalog)

        assert len([c for c in result.candidates if 'Sapiens' in c.entry.title]) == 2


class TestMarginIsTheGate:
    """Why margin exists at all. Write these first; watch score alone fail."""

    def test_a_high_score_over_a_tie_is_not_a_match(self, catalog):
        """The whole argument in one test.

        Score says yes. Margin says it is a coin flip. Auto-accept must side
        with margin -- a scorer gated on score alone would add the wrong
        edition here and never tell anyone.
        """
        result = match('Sapiens', 'Yuval Noah Harari', catalog)

        assert result.score >= AUTO_ACCEPT_SCORE
        assert result.margin < AUTO_ACCEPT_MARGIN
        assert not result.should_auto_accept

    def test_a_clear_winner_passes_both_gates(self, catalog):
        result = match('Neuromancer', 'William Gibson', catalog)

        assert result.score >= AUTO_ACCEPT_SCORE
        assert result.margin >= AUTO_ACCEPT_MARGIN
        assert result.should_auto_accept

    def test_a_lone_candidate_has_nothing_competing_with_it(self, catalog):
        """With no runner-up the margin is the score: nothing contested it."""
        result = match('Braiding Sweetgrass', 'Robin Wall Kimmerer', catalog)

        assert len(result.candidates) == 1
        assert result.margin == result.score


class TestAuthorlessReads:
    def test_a_read_with_no_author_can_never_auto_accept(self, catalog):
        """Structural, not incidental: the cap sits below the accept threshold."""
        assert NO_AUTHOR_SCORE_CAP < AUTO_ACCEPT_SCORE

        result = match('Neuromancer', '', catalog)

        assert result.best.entry.title == 'Neuromancer'
        assert result.score <= NO_AUTHOR_SCORE_CAP
        assert not result.should_auto_accept

    def test_an_author_that_disagrees_is_not_the_same_as_no_author(self, catalog):
        missing = match('Neuromancer', '', catalog)
        wrong = match('Neuromancer', 'Jane Austen', catalog)

        assert missing.best.author_score is None
        assert wrong.best.author_score == 0.0
        assert wrong.score < missing.score


class TestNormalizationReachesTheMatcher:
    def test_accents_fold(self, catalog):
        result = match(
            'One Hundred Years of Solitude', 'Gabriel Garcia Marquez', catalog
        )

        assert result.should_auto_accept

    def test_initials_collapse(self, catalog):
        result = match('The Silmarillion', 'JRR Tolkien', catalog)

        assert result.should_auto_accept

    def test_leading_articles_are_optional(self, catalog):
        with_article = match('The Dispossessed', 'Ursula K. Le Guin', catalog)
        without = match('Dispossessed', 'Ursula K. Le Guin', catalog)

        assert with_article.best.entry.title == 'The Dispossessed'
        assert without.best.entry.title == 'The Dispossessed'


class TestGarbageIn:
    def test_an_unreadable_spine_matches_nothing(self, catalog):
        assert match('xqzv wwbb', '', catalog).best is None

    def test_an_empty_read_matches_nothing(self, catalog):
        assert match('', '', catalog).best is None
        assert match('   ', '', catalog).candidates == ()
