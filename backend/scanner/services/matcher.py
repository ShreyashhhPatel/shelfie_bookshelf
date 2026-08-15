"""Resolve a spine read to a canonical catalog entry.

This is the part of the product that is actually hard. Detection is a
pretrained checkpoint and reading is an API call; deciding *which book* an
imperfect read refers to, and knowing when not to decide, is the work.

Two rules run through everything here:

1. Never let one signal decide alone. A title can be shared by two books
   (The Idiot) and an author can be shared by two people (David Mitchell).
2. A high score is not a confident answer. What matters is whether the winner
   beat the runner-up, which is why `margin` exists and why auto-accept
   requires it.

No network, no model, no database. `match()` takes plain values so every rule
below is unit-testable in isolation -- see scanner/tests/test_matcher.py, which
has one case per ambiguity documented in catalog/AMBIGUITIES.md.
"""

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from rapidfuzz import fuzz

from ..constants import (
    AUTHOR_WEIGHT,
    AUTO_ACCEPT_MARGIN,
    AUTO_ACCEPT_SCORE,
    INITIALS_WEIGHT,
    MAIN_TITLE_MATCH_PENALTY,
    MAX_CANDIDATES,
    MIN_CANDIDATE_SCORE,
    NO_AUTHOR_SCORE_CAP,
    SURNAME_MISMATCH_THRESHOLD,
    SURNAME_WEIGHT,
    TITLE_WEIGHT,
    author_surname,
    main_title,
    normalize_author,
    normalize_title,
)


@dataclass(frozen=True)
class CatalogEntry:
    """A catalog row, detached from Django.

    `contained_titles` is deliberately absent. An omnibus must not be matchable
    by the names of the books inside it -- AMBIGUITIES.md case 3 -- and the
    surest way to guarantee that is to give the matcher no way to see them.
    """

    id: int | None
    title: str
    author: str
    alt_titles: tuple[str, ...] = ()
    is_omnibus: bool = False

    @property
    def all_titles(self) -> tuple[str, ...]:
        return (self.title, *self.alt_titles)

    @classmethod
    def from_model(cls, book) -> 'CatalogEntry':
        return cls(
            id=book.pk,
            title=book.title,
            author=book.author,
            alt_titles=tuple(book.alt_titles or ()),
            is_omnibus=book.is_omnibus,
        )


@dataclass(frozen=True)
class Candidate:
    """One scored possibility for a spine."""

    entry: CatalogEntry
    score: float
    title_score: float
    #: None when the read carried no author at all, which is not the same as an
    #: author that was read and disagreed (0.0).
    author_score: float | None
    #: Which of the entry's titles actually matched. The review screen shows
    #: this so a reader holding "The Golden Compass" is not told "Northern
    #: Lights" with no explanation.
    matched_title: str

    def as_dict(self) -> dict:
        """Shape stored in `Detection.candidates`."""
        return {
            'catalog_book_id': self.entry.id,
            'title': self.entry.title,
            'author': self.entry.author,
            'matched_title': self.matched_title,
            'score': round(self.score, 4),
            'title_score': round(self.title_score, 4),
            'author_score': (
                None if self.author_score is None else round(self.author_score, 4)
            ),
        }


@dataclass(frozen=True)
class MatchResult:
    candidates: tuple[Candidate, ...] = ()
    #: Gap between the best and second-best score. When only one candidate
    #: survives, nothing competed, so the margin is the score itself.
    margin: float = 0.0

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0

    @property
    def should_auto_accept(self) -> bool:
        """Both gates, deliberately.

        Score says the winner looks right. Margin says nothing else looks just
        as right. Dropping either one is how a shelf of Dune sequels turns into
        five copies of Dune.
        """
        return self.score >= AUTO_ACCEPT_SCORE and self.margin >= AUTO_ACCEPT_MARGIN

    def as_dicts(self) -> list[dict]:
        return [candidate.as_dict() for candidate in self.candidates]


def _ratio(left: str, right: str) -> float:
    """Symmetric similarity in 0..1.

    `token_sort_ratio` rather than `partial_ratio` or `WRatio`, and the choice
    is load-bearing. Partial matching scores a substring as perfect, so "Dune"
    would score 1.0 against "Dune Messiah", "Children of Dune", and three more
    -- exactly the collision this matcher exists to resolve. Token sort stays
    symmetric: unmatched words on the catalog side cost as much as unmatched
    words on the read side.
    """
    if not left or not right:
        return 0.0
    return fuzz.token_sort_ratio(left, right) / 100.0


def score_title(read_title: str, entry: CatalogEntry) -> tuple[float, str]:
    """Best score across the entry's canonical and alternate titles.

    An alternate-title hit is full strength -- the US edition genuinely does
    print "The Golden Compass" on the spine, and there is nothing second-rate
    about matching it.
    """
    read_norm = normalize_title(read_title)
    if not read_norm:
        return 0.0, entry.title

    best_score = 0.0
    best_title = entry.title

    for candidate_title in entry.all_titles:
        candidate_norm = normalize_title(candidate_title)

        if candidate_norm == read_norm:
            return 1.0, candidate_title

        score = _ratio(read_norm, candidate_norm)

        # Fallback for a spine that printed only the main title. Discounted,
        # because collapsing "Sapiens: A Brief History of Humankind" to
        # "Sapiens" is exactly what makes it collide with the graphic edition.
        main_norm = normalize_title(main_title(candidate_title))
        if main_norm != candidate_norm:
            main_score = _ratio(read_norm, main_norm) * MAIN_TITLE_MATCH_PENALTY
            score = max(score, main_score)

        if score > best_score:
            best_score, best_title = score, candidate_title

    return best_score, best_title


def _initials(normalized_author: str) -> str:
    """First letters of everything before the surname.

    Runs of initials are already glued together by `normalize_author`, so
    "J.R.R. Tolkien" arrives as "jrr tolkien" and yields "j" -- the same as
    "John Tolkien" would. Initials are weak evidence and are treated as such.
    """
    tokens = normalized_author.split()
    return ''.join(token[0] for token in tokens[:-1])


def score_author(read_author: str, catalog_author: str) -> float | None:
    """Structured comparison on surname and initials.

    Returns None when nothing was read -- distinct from 0.0, which means an
    author *was* read and disagreed. The caller treats those very differently.

    Not a plain fuzzy ratio, because the failure modes are structural rather
    than typographic: spines routinely print the surname alone, and two authors
    who share a surname differ only in their initials.
    """
    if not read_author or not read_author.strip():
        return None

    read_norm = normalize_author(read_author)
    catalog_norm = normalize_author(catalog_author)
    if not read_norm or not catalog_norm:
        return None

    surname = _ratio(author_surname(read_norm), author_surname(catalog_norm))

    # Two unrelated surnames still share letters, and counting that as partial
    # credit makes a confidently wrong author better evidence than no author.
    # Below the floor they are simply different people.
    if surname < SURNAME_MISMATCH_THRESHOLD:
        return 0.0

    read_initials = _initials(read_norm)
    catalog_initials = _initials(catalog_norm)

    # A surname-only read ("TOLKIEN" is all the spine prints) must not be
    # punished for initials it never claimed to have. Only compare them when
    # both sides actually offer some.
    if not read_initials or not catalog_initials:
        return surname

    initials = 1.0 if read_initials == catalog_initials else _ratio(
        read_initials, catalog_initials
    )
    return SURNAME_WEIGHT * surname + INITIALS_WEIGHT * initials


def combine(title_score: float, author_score: float | None) -> float:
    """Blend the two signals into one score.

    With no author, the score is capped rather than reweighted. The cap sits
    below AUTO_ACCEPT_SCORE on purpose, which makes a structural guarantee out
    of it: **a read with no author can never be auto-accepted.** "The Idiot"
    with no author is a perfect title match to two different books, and the
    only honest response is to ask.
    """
    if author_score is None:
        return min(title_score, NO_AUTHOR_SCORE_CAP)
    return TITLE_WEIGHT * title_score + AUTHOR_WEIGHT * author_score


def match(
    read_title: str,
    read_author: str,
    entries: Sequence[CatalogEntry],
    limit: int = MAX_CANDIDATES,
) -> MatchResult:
    """Score a spine read against the catalog and rank what it could be."""
    if not read_title or not read_title.strip():
        return MatchResult()

    scored: list[Candidate] = []
    for entry in entries:
        title_score, matched_title = score_title(read_title, entry)
        if title_score <= 0.0:
            continue
        author_score = score_author(read_author, entry.author)
        score = combine(title_score, author_score)
        if score < MIN_CANDIDATE_SCORE:
            continue
        scored.append(
            Candidate(
                entry=entry,
                score=score,
                title_score=title_score,
                author_score=author_score,
                matched_title=matched_title,
            )
        )

    if not scored:
        return MatchResult()

    # Title score breaks ties in the blended score, so a genuine exact title
    # hit outranks a fuzzy title propped up by a confident author.
    scored.sort(key=lambda c: (c.score, c.title_score), reverse=True)
    top = tuple(scored[:limit])

    margin = top[0].score - top[1].score if len(top) > 1 else top[0].score

    return MatchResult(candidates=top, margin=margin)


def catalog_entries() -> list[CatalogEntry]:
    """Load the whole catalog as plain entries.

    Imported lazily so that importing this module never drags in Django. The
    catalog is small and read-mostly; when it stops being either, this is the
    single place that has to learn about caching.
    """
    from ..models import CatalogBook

    return [CatalogEntry.from_model(book) for book in CatalogBook.objects.all()]
