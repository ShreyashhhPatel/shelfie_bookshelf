"""
Matches a VLM-read (title, author) pair against the catalog.

The differentiating idea: confidence is *separation*, not just similarity.
We take the top candidate's blended score AND its margin over the
second-best candidate. Auto-accept only when both are high; otherwise route
to review. See scanner/constants.py for the thresholds and catalog/AMBIGUITIES.md
for the specific cases this is designed to handle.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from scanner.constants import (
    AUTHOR_WEIGHT,
    AUTO_ACCEPT_MARGIN_THRESHOLD,
    AUTO_ACCEPT_SCORE_THRESHOLD,
    COLOR_TIEBREAK_MAX_MARGIN,
    LEADING_ARTICLES,
    REVIEW_CANDIDATE_COUNT,
    REVIEW_MIN_SCORE_THRESHOLD,
    SURNAME_FUZZY_MATCH_THRESHOLD,
    TITLE_WEIGHT,
    UNREADABLE_AUTHOR_CONFIDENCE_CAP,
)

_APOSTROPHE_RE = re.compile(r"[‘’'`]")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_GLUED_INITIAL_RE = re.compile(r"\.(?=\S)")

STATUS_AUTO_ADDED = "auto_added"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_UNMATCHED = "unmatched"


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str | None) -> str:
    """Lowercase, strip accents/punctuation, drop a leading article."""
    if not text:
        return ""
    text = strip_accents(text).lower()
    text = _APOSTROPHE_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return text
    words = text.split(" ")
    if words[0] in LEADING_ARTICLES and len(words) > 1:
        words = words[1:]
    return " ".join(words)


@dataclass(frozen=True)
class AuthorName:
    raw: str
    surname: str
    initials: tuple[str, ...] = field(default_factory=tuple)


def normalize_author(raw: str | None) -> AuthorName | None:
    """
    Parses "Firstname Lastname", "Lastname, Firstname", and glued-initial
    forms ("J.K. Rowling") into a comparable (surname, initials) shape.
    Accents are stripped first so e.g. "Garcia" and "García" normalize the
    same way.
    """
    if not raw or not raw.strip():
        return None
    text = strip_accents(raw).strip()
    text = _GLUED_INITIAL_RE.sub(". ", text)
    text = _WS_RE.sub(" ", text).strip()

    if "," in text:
        surname_part, given_part = (p.strip() for p in text.split(",", 1))
        surname_tokens = _PUNCT_RE.sub(" ", surname_part).lower().split()
        given_tokens = _PUNCT_RE.sub(" ", given_part).lower().split()
    else:
        tokens = _PUNCT_RE.sub(" ", text).lower().split()
        if not tokens:
            return None
        surname_tokens = tokens[-1:]
        given_tokens = tokens[:-1]

    surname = " ".join(surname_tokens)
    if not surname:
        return None
    initials = tuple(tok[0] for tok in given_tokens if tok)
    return AuthorName(raw=raw, surname=surname, initials=initials)


def title_score(read_title: str | None, catalog_title: str, alt_titles: list[str] | None = None) -> float:
    """Fuzzy match against the canonical title AND every alternate title; best wins."""
    read_norm = normalize_text(read_title)
    if not read_norm:
        return 0.0
    candidates = [catalog_title, *(alt_titles or [])]
    best = 0.0
    for candidate in candidates:
        candidate_norm = normalize_text(candidate)
        if not candidate_norm:
            continue
        score = fuzz.WRatio(read_norm, candidate_norm) / 100.0
        best = max(best, score)
    return best


def author_score(read_author: str | None, catalog_author: str | None) -> float | None:
    """
    Structured, not fuzzy: compare surnames first, then check initial
    compatibility. Returns None (unknown) when either side couldn't be
    parsed into a name, letting the caller decide how to blend that.
    """
    read = normalize_author(read_author)
    catalog = normalize_author(catalog_author)
    if read is None or catalog is None:
        return None

    if read.surname == catalog.surname:
        surname_component = 1.0
    else:
        surname_component = fuzz.ratio(read.surname, catalog.surname) / 100.0
        if surname_component < SURNAME_FUZZY_MATCH_THRESHOLD:
            return 0.0

    if not read.initials or not catalog.initials:
        return surname_component

    overlap = min(len(read.initials), len(catalog.initials))
    compatible = all(read.initials[i] == catalog.initials[i] for i in range(overlap))
    return surname_component if compatible else surname_component * 0.5


def blend(title_score_value: float, author_score_value: float | None) -> float:
    if author_score_value is None:
        return min(title_score_value, UNREADABLE_AUTHOR_CONFIDENCE_CAP)
    return TITLE_WEIGHT * title_score_value + AUTHOR_WEIGHT * author_score_value


@dataclass
class Candidate:
    book: dict
    score: float
    title_score: float
    author_score: float | None


@dataclass
class MatchResult:
    status: str
    confidence: float | None
    margin: float | None
    candidates: list[Candidate]


def match_book(
    read_title: str | None,
    read_author: str | None,
    catalog: list[dict],
    top_n: int = REVIEW_CANDIDATE_COUNT,
) -> MatchResult:
    """
    Scores `read_title`/`read_author` against every catalog row and buckets
    the result. `catalog` rows are plain dicts with at least `title` and
    `author`, and optionally `alt_titles` (list[str]).
    """
    if not read_title or not read_title.strip():
        return MatchResult(status=STATUS_UNMATCHED, confidence=None, margin=None, candidates=[])

    scored: list[Candidate] = []
    for book in catalog:
        t_score = title_score(read_title, book["title"], book.get("alt_titles"))
        a_score = author_score(read_author, book.get("author"))
        scored.append(
            Candidate(
                book=book,
                score=blend(t_score, a_score),
                title_score=t_score,
                author_score=a_score,
            )
        )
    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[:top_n]

    if not top or top[0].score < REVIEW_MIN_SCORE_THRESHOLD:
        return MatchResult(
            status=STATUS_UNMATCHED,
            confidence=top[0].score if top else None,
            margin=None,
            candidates=top,
        )

    confidence = top[0].score
    margin = confidence - (top[1].score if len(top) > 1 else 0.0)

    if confidence >= AUTO_ACCEPT_SCORE_THRESHOLD and margin >= AUTO_ACCEPT_MARGIN_THRESHOLD:
        status = STATUS_AUTO_ADDED
    else:
        status = STATUS_NEEDS_REVIEW

    return MatchResult(status=status, confidence=confidence, margin=margin, candidates=top)


def apply_color_tiebreak(result: MatchResult, read_color: str | None) -> MatchResult:
    """
    Re-orders candidates that the text scores left in a near-tie, putting the
    one whose spine colour matches the photo first.

    Deliberately presentation-only: status, confidence and margin are all left
    exactly as the text evidence set them. Colour is a weak signal read off a
    crop under unknown lighting, and letting it promote anything to
    auto-accept would put wrong books in the library with no human step. What
    it does earn is the top slot on a review card -- which is the whole
    difference between "pick one of these two identical titles" and "yes,
    that one".
    """
    if not read_color or result.margin is None or result.margin > COLOR_TIEBREAK_MAX_MARGIN:
        return result

    leader = result.candidates[0].score
    tied = [c for c in result.candidates if leader - c.score <= COLOR_TIEBREAK_MAX_MARGIN]
    if len(tied) < 2:
        return result

    matches = [c for c in tied if c.book.get("spine_color") and c.book["spine_color"] == read_color]
    # Exactly one colour match is a decision. Zero means the catalog has no
    # colour for these; more than one means colour didn't separate them either.
    if len(matches) != 1:
        return result

    winner = matches[0]
    reordered = [winner] + [c for c in result.candidates if c is not winner]
    return MatchResult(
        status=result.status,
        confidence=result.confidence,
        margin=result.margin,
        candidates=reordered,
    )
