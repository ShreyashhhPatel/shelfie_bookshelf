"""
Every tunable threshold, weight, and provider setting lives here as a named
constant. Per CONTEXT.md, the live 30-minute session includes one small code
change (e.g. "make matching stricter" or "add a field") — keeping these
values named and centralized is what makes that a one-line edit.
"""

# --- Matching: title/author blend ---
TITLE_WEIGHT = 0.75
AUTHOR_WEIGHT = 0.25

# Applied when the VLM could not read an author at all: title-only matches
# are capped below this so they can never auto-accept on title alone.
UNREADABLE_AUTHOR_CONFIDENCE_CAP = 0.85

# Minimum surname similarity (0-1) to treat two author surnames as the same
# person despite OCR noise, rather than as a mismatch.
SURNAME_FUZZY_MATCH_THRESHOLD = 0.85

# --- Matching: confidence + margin bucketing ---
# Auto-add requires both a high top score AND a clear lead over the runner-up
# candidate. This is the "confidence is separation, not just similarity" rule.
AUTO_ACCEPT_SCORE_THRESHOLD = 0.90
AUTO_ACCEPT_MARGIN_THRESHOLD = 0.12

# Below this score there's nothing plausible to show a reviewer -> unmatched.
REVIEW_MIN_SCORE_THRESHOLD = 0.55

# How many ranked candidates to surface on a review card.
REVIEW_CANDIDATE_COUNT = 3

# --- Text normalization ---
LEADING_ARTICLES = ("the", "a", "an")

# --- YOLO detection ---
YOLO_MODEL_NAME = "yolov8x.pt"
YOLO_CONFIDENCE_THRESHOLD = 0.25
YOLO_BOOK_CLASS_NAME = "book"
CROP_PADDING_PX = 6

# --- YOLO detection: over-counting control ---
# Everything below exists to stop one physical book becoming several rows.
# Ultralytics' own NMS, tightened from its 0.7 default: two boxes drawn on a
# single tall spine overlap far less than two boxes on a general-purpose
# object, so the default happily keeps both.
YOLO_NMS_IOU_THRESHOLD = 0.5

# A shelved spine is tall and narrow. Squat boxes are the shelf itself, a
# cardboard carton, or background clutter -- not a book to spend a VLM call
# on. Assumes upright spines, which the whole pipeline already does (see
# TALL_NARROW_ASPECT_RATIO). If this filter would empty the frame, detection
# keeps the unfiltered set rather than reporting an empty shelf.
MIN_SPINE_ASPECT_RATIO = 2.5

# Boxes thinner than this fraction of the image width are a sliver of a spine
# (a title block, a shadow) rather than a spine.
MIN_SPINE_WIDTH_RATIO = 0.015

# Two boxes are the same spine when they overlap this much...
DEDUP_IOU_THRESHOLD = 0.55

# ...or when one sits essentially inside the other AND the two are of
# comparable width. That width guard is load-bearing: a narrow box inside a
# wide one is usually a real neighbouring spine that an over-wide box
# swallowed, and dropping it would lose a book the user owns.
DEDUP_CONTAINMENT_THRESHOLD = 0.80
DEDUP_MIN_WIDTH_RATIO = 0.60

# Last word on any merge: a near-full-height vertical edge running through the
# overlap is two physical spines meeting, so they stay separate however much
# their boxes overlap.
VERTICAL_DIVIDER_MIN_COLUMN_RATIO = 0.30
VERTICAL_DIVIDER_CANNY_LOW = 50
VERTICAL_DIVIDER_CANNY_HIGH = 150

# A boundary is a *peak* in edge density, not merely a busy region: dense
# cover art and sensor noise in a dark photo light up every column about
# equally, and reading that as a spine edge would block a real merge and put
# the over-counting straight back.
VERTICAL_DIVIDER_PEAK_RATIO = 2.0
# Crops taller than this height/width ratio are rotated 90 degrees before
# reading, since spine text runs vertically on a shelf photo.
TALL_NARROW_ASPECT_RATIO = 1.15

# --- Spine colour ---
# A closed vocabulary, not free text. Asked to name a colour, a VLM answers
# "mustard", "canary", "lemon" -- all correct, none of which equal the
# catalog's "yellow". Constraining the prompt to this list is what makes the
# read comparable to CatalogBook.spine_color at all.
SPINE_COLOR_VOCABULARY = (
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "pink",
    "brown",
    "black",
    "white",
    "gray",
    "beige",
    "gold",
    "silver",
    "multicolour",
)

# Spellings and near-misses folded into the vocabulary above. Anything still
# unrecognised after this is dropped rather than stored, so a colour is either
# comparable or absent -- never a value nothing can match.
SPINE_COLOR_SYNONYMS = {
    "grey": "gray",
    "multicolor": "multicolour",
    "multicolored": "multicolour",
    "multicoloured": "multicolour",
    "multi-colour": "multicolour",
    "multi-color": "multicolour",
    "cream": "beige",
    "tan": "beige",
    "navy": "blue",
    "teal": "blue",
    "maroon": "red",
    "burgundy": "red",
    "violet": "purple",
    "lilac": "purple",
}

# Colour never decides a match on its own -- it only re-ranks candidates the
# title and author scores have already left in a near-tie. Above this margin
# the text evidence is clear enough that colour must not interfere.
COLOR_TIEBREAK_MAX_MARGIN = 0.05

# --- VLM ---
# The reader is provider-pluggable (settings.VLM_PROVIDER). Prompts, JSON
# parsing, retry semantics and failure statuses are shared; only the transport
# differs, so the measured numbers in the README stay reproducible on Gemini.
# Pinned, not the "gemini-flash-latest" alias: the alias drifts under you, and
# the measured timings/costs in the README are only reproducible against a fixed
# model. gemini-2.5-flash is no longer served to new API projects.
GEMINI_MODEL = "gemini-3.5-flash-lite"
CLAUDE_MODEL = "claude-opus-5"
# Spine reading is short-answer perception, not deep reasoning: low effort keeps
# latency and token spend down. Thinking stays on (disabling it on Opus 5 can
# leak <thinking> tags into the response, which would break JSON parsing).
CLAUDE_EFFORT = "low"
CLAUDE_MAX_TOKENS = 8000
VLM_TIMEOUT_SECONDS = 45
VLM_MAX_RETRIES = 1
VLM_RETRY_BACKOFF_SECONDS = 2
