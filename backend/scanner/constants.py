"""Tunable constants and the text normalization the pipeline shares.

Every number the pipeline can be tuned by lives here rather than inline at its
call site, so changing behaviour means editing one file and reading one diff.

Comparison never happens on raw strings. A spine read by the VLM carries
whatever casing, punctuation, and accents the cover designer used; the catalog
carries whatever the publisher used. Both sides go through the same functions
here so the two are actually comparable.

Nothing in this module imports models, torch, or the network. It is pure text
and numbers, so the matcher can be unit-tested against it without a database.
"""

import re
import unicodedata
from pathlib import Path

# Resolved from this file rather than settings.BASE_DIR so that importing
# constants never requires Django to be configured first.
BACKEND_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# Spine detection (phase 3)
# --------------------------------------------------------------------------

# Nano is the smallest YOLOv8 checkpoint. It is chosen for CPU latency, not
# accuracy: detection has to be local and free so that the VLM read is the only
# thing that costs money per scan. Swap to yolov8s.pt if recall proves too low.
YOLO_MODEL_NAME = 'yolov8n.pt'
YOLO_MODEL_DIR = BACKEND_DIR / 'models'
YOLO_MODEL_PATH = YOLO_MODEL_DIR / YOLO_MODEL_NAME

# COCO class 73. The checkpoint is pretrained on all 80 classes and a shelf
# photo will happily return vases and potted plants, so everything else is
# discarded before the boxes leave the detector.
BOOK_CLASS_ID = 73

# Deliberately low. A missed spine is invisible to the user and unrecoverable;
# a false positive costs one VLM call and is caught at the review step. Recall
# is worth more than precision here.
DETECTION_CONFIDENCE_THRESHOLD = 0.15

# IoU for non-max suppression. Shelved books touch, so boxes legitimately
# overlap and the default 0.7 merges neighbouring spines into one.
DETECTION_IOU_THRESHOLD = 0.45

# A shelf photo can hold a lot of books; the ultralytics default of 300 is
# generous enough, but stated here so it is not a hidden ceiling.
MAX_DETECTIONS_PER_IMAGE = 300

# --------------------------------------------------------------------------
# Crop preparation (phase 3)
# --------------------------------------------------------------------------

# YOLO boxes clip tight to the spine and often shave the first or last glyph.
# A few pixels of margin costs nothing and recovers characters the VLM needs.
CROP_PADDING_PX = 6

# Height/width above which a crop is treated as a vertical spine and rotated
# upright. 1.6 is loose on purpose: a book photographed at an angle is less
# tall-narrow than one shot square on, and under-rotating is the worse error.
TALL_NARROW_ASPECT_RATIO = 1.6

# Rotating clockwise puts the spine's foot at the left, which is how the large
# majority of English-language spines are printed.
SPINE_ROTATION_DEGREES = -90

# Longest edge a crop is scaled up to before being sent for reading. Small
# crops are the main cause of unreadable spines; upscaling past this stops
# helping and just costs bytes.
CROP_TARGET_LONG_EDGE = 640

# Hosted vision models bill by image bytes, and a shelf photo straight off a
# modern phone is far larger than any of them need.
JPEG_QUALITY = 90
MAX_IMAGE_LONG_EDGE = 2048

# --------------------------------------------------------------------------
# Catalog matching (phase 4)
# --------------------------------------------------------------------------

# Title carries the weight because it is what a spine reliably prints large.
# Author is corroboration, not identification -- see AMBIGUITIES.md case 5,
# where two different people share the name David Mitchell.
TITLE_WEIGHT = 0.75
AUTHOR_WEIGHT = 0.25

# A read with no author at all cannot exceed this, no matter how perfect the
# title match. "The Idiot" scores 1.0 on title against two different books
# (case 4); without an author there is genuinely no way to choose, and the
# honest output is a capped score that fails the auto-accept gate below.
NO_AUTHOR_SCORE_CAP = 0.80

# Within the author sub-score: a surname is far more legible on a spine than
# initials and is weighted accordingly, but initials are what separate two
# writers who share a surname.
SURNAME_WEIGHT = 0.7
INITIALS_WEIGHT = 0.3

# Below this, two surnames are different people and the author contributes
# nothing. Without a floor, fuzzy similarity between unrelated names ("austen"
# vs "gibson" shares enough letters to score ~0.33) leaks in as partial credit,
# and a confidently *wrong* author ends up scoring higher than no author at
# all -- which is backwards. Above it, near-misses are treated as the OCR
# errors they usually are.
SURNAME_MISMATCH_THRESHOLD = 0.55

# A spine that prints only the main title ("SAPIENS", with the subtitle set too
# small to survive the crop) still has to find its rows. Matching against
# main_title() is allowed, but discounted: it threw information away, so it may
# surface a candidate for review and must never look as certain as a full hit.
MAIN_TITLE_MATCH_PENALTY = 0.90

# Below this a candidate is not worth showing the user even as a correction
# option. Purely a noise filter.
MIN_CANDIDATE_SCORE = 0.40

# How many ranked candidates to keep on a Detection. Enough to populate a
# review screen's correction list without storing the whole catalog.
MAX_CANDIDATES = 5

# The auto-accept gate. BOTH must hold.
#
# Score alone is not sufficient and this is the central claim of the matcher:
# "Dune" scores ~1.0 against the row Dune *and* highly against Dune Messiah,
# Children of Dune, and three more (case 1). A confident-looking top score
# over a crowded field is a coin flip. The margin -- the gap to the runner-up
# -- is what says the winner actually won.
AUTO_ACCEPT_SCORE = 0.82
AUTO_ACCEPT_MARGIN = 0.12

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
