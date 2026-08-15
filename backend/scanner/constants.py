"""Text normalization shared by catalog loading and spine matching.

Comparison never happens on raw strings. A spine read by the VLM carries
whatever casing, punctuation, and accents the cover designer used; the catalog
carries whatever the publisher used. Both sides go through the same functions
here so the two are actually comparable.

Nothing in this module knows about models or matching scores. It is pure text
in, pure text out, so the matcher in a later phase can be tested against it
without a database.
"""

import re
import unicodedata

# Dropped from the front of a title so "The Hobbit" and "Hobbit" collide. Only
# ever stripped from the leading position: "A Wizard of Earthsea" loses its
# "A", but "Notes from Underground" keeps every word.
LEADING_ARTICLES = ('the', 'a', 'an')

# A publisher's subtitle is the least reliable thing on a spine -- it is set in
# the smallest type and is the first thing a cropped or angled photo loses.
SUBTITLE_SEPARATORS = (':', ' - ', ' – ', ' — ')

_PUNCTUATION = re.compile(r'[^\w\s]', re.UNICODE)
_WHITESPACE = re.compile(r'\s+')


def strip_accents(value: str) -> str:
    """Fold accented characters to their base letters.

    "Gabriel Garcia Marquez" typed by a reader and "Gabriel García Márquez"
    printed on the spine have to land on the same key.
    """
    decomposed = unicodedata.normalize('NFKD', value)
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace."""
    if not value:
        return ''
    folded = strip_accents(value).lower()
    folded = _PUNCTUATION.sub(' ', folded)
    return _WHITESPACE.sub(' ', folded).strip()


def normalize_title(value: str, drop_article: bool = True) -> str:
    """Normalize a title for matching.

    Subtitles are deliberately kept. Two books can share a main title and
    differ only after the colon, so dropping the subtitle here would silently
    merge distinct catalog entries -- see main_title() for the lossy version
    and AMBIGUITIES.md for the case this protects.
    """
    normalized = normalize_text(value)
    if drop_article:
        head, _, tail = normalized.partition(' ')
        if tail and head in LEADING_ARTICLES:
            normalized = tail
    return normalized


def main_title(value: str) -> str:
    """The part of a title before its subtitle separator.

    Lossy on purpose, and only safe as a fallback after an exact normalized
    match has already failed. Use it to widen a search, never to decide one.
    """
    for separator in SUBTITLE_SEPARATORS:
        index = value.find(separator)
        if index > 0:
            return value[:index].strip()
    return value.strip()


def collapse_initials(value: str) -> str:
    """Join runs of single-letter tokens into one.

    Dropping punctuation turns "J.R.R." into "j r r", which no longer matches a
    reader who typed "JRR". Runs of one-letter tokens are therefore glued back
    together: "j r r tolkien" -> "jrr tolkien". A lone initial is left as-is,
    so "ursula k le guin" keeps its shape.
    """
    joined: list[str] = []
    run: list[str] = []
    for token in value.split():
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue
        if run:
            joined.append(''.join(run))
            run = []
        joined.append(token)
    if run:
        joined.append(''.join(run))
    return ' '.join(joined)


def normalize_author(value: str) -> str:
    """Normalize an author for matching.

    Handles the "Last, First" form some catalogs use, and reduces initials to
    bare letters so "J.R.R. Tolkien", "J R R Tolkien", and "JRR Tolkien" agree.
    """
    if not value:
        return ''
    if ',' in value:
        last, _, first = value.partition(',')
        value = f'{first.strip()} {last.strip()}'
    return collapse_initials(normalize_text(value))


def author_surname(value: str) -> str:
    """Last token of a normalized author name.

    A spine often prints only the surname, so this is the coarsest key the
    matcher can fall back to. It is also the weakest: surnames collide, and two
    living writers can share a full name, let alone a last one.
    """
    normalized = normalize_author(value)
    return normalized.rsplit(' ', 1)[-1] if normalized else ''


def split_multi(value: str, separator: str = '|') -> list[str]:
    """Split a packed CSV cell into a clean list.

    The catalog stores repeated values (alternate titles, the contents of an
    omnibus) pipe-separated in a single cell, because commas are load-bearing
    in the file format and common inside titles.
    """
    if not value:
        return []
    return [part.strip() for part in value.split(separator) if part.strip()]
