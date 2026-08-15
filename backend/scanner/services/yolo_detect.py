"""
Local YOLOv8x spine detection: locates each book spine's bounding box on the
shelf photo (CPU, no cost, ~5-10x faster than Faster R-CNN on CPU). Does not
read anything -- that's the batched VLM call's job.

Raw YOLO over-counts badly on a shelf photo: it draws several boxes down one
spine, and it calls background clutter and cardboard cartons "book". Both
inflate the review queue and cost a VLM image token each, so the raw boxes go
through three passes before anyone sees them -- shape, then slivers, then
duplicate merging. Every threshold is named in scanner/constants.py.

The one thing this must never do is merge two *different* books. So merging is
gated on a pixel check (is there a real spine edge between them?) and on a
width check (is the smaller box actually a neighbour the bigger box swallowed?),
and both have to agree before a box is dropped.
"""

import logging
import threading

import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

from scanner.constants import (
    DEDUP_CONTAINMENT_THRESHOLD,
    DEDUP_IOU_THRESHOLD,
    DEDUP_MIN_WIDTH_RATIO,
    MIN_SPINE_ASPECT_RATIO,
    MIN_SPINE_WIDTH_RATIO,
    VERTICAL_DIVIDER_CANNY_HIGH,
    VERTICAL_DIVIDER_CANNY_LOW,
    VERTICAL_DIVIDER_MIN_COLUMN_RATIO,
    VERTICAL_DIVIDER_PEAK_RATIO,
    YOLO_BOOK_CLASS_NAME,
    YOLO_CONFIDENCE_THRESHOLD,
    YOLO_MODEL_NAME,
    YOLO_NMS_IOU_THRESHOLD,
)

logger = logging.getLogger(__name__)

Box = tuple[int, int, int, int]

_model: YOLO | None = None
_model_lock = threading.Lock()


def _get_model() -> YOLO:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = YOLO(YOLO_MODEL_NAME)
    return _model


# --- geometry -------------------------------------------------------------


def area(box: Box) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def intersection_area(a: Box, b: Box) -> int:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def iou(a: Box, b: Box) -> float:
    """Standard intersection over union."""
    inter = intersection_area(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def containment(a: Box, b: Box) -> float:
    """
    Intersection over the *smaller* box's area: "how much of the smaller box
    lies inside the bigger one". IoU misses this case badly -- a title-sized
    box sitting wholly inside a full-spine box scores ~0.3 IoU but 1.0 here.
    """
    smaller = min(area(a), area(b))
    return intersection_area(a, b) / smaller if smaller > 0 else 0.0


def union_box(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def width_ratio(a: Box, b: Box) -> float:
    """Width of the narrower box over the wider one; 1.0 means equal width."""
    wa, wb = a[2] - a[0], b[2] - b[0]
    wider = max(wa, wb)
    return min(wa, wb) / wider if wider > 0 else 0.0


def is_spine_shaped(box: Box) -> bool:
    """A shelved spine is taller than it is wide, by a good margin."""
    width, height = box[2] - box[0], box[3] - box[1]
    return width > 0 and height / width >= MIN_SPINE_ASPECT_RATIO


# --- pixel check ----------------------------------------------------------


def has_vertical_divider(gray: np.ndarray, a: Box, b: Box) -> bool:
    """
    True when a near-full-height vertical edge runs through the overlap of two
    boxes -- the shadow line where one spine meets the next. It is the signal
    that separates "YOLO boxed one book twice" from "two books are touching",
    which no amount of box arithmetic can tell apart.
    """
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 - x1 < 2 or y2 - y1 < 2:
        return False

    region = gray[y1:y2, x1:x2]
    if region.size == 0:
        return False

    edges = cv2.Canny(region, VERTICAL_DIVIDER_CANNY_LOW, VERTICAL_DIVIDER_CANNY_HIGH)
    # Count edge pixels per column: a spine boundary lights up one column down
    # most of its height, while cover art and text scatter across many.
    column_hits = (edges > 0).sum(axis=0)
    if column_hits.size == 0:
        return False

    peak = int(column_hits.max())
    if peak < region.shape[0] * VERTICAL_DIVIDER_MIN_COLUMN_RATIO:
        return False

    # ...and it has to stand out from its surroundings. Without this, a busy
    # cover or a noisy low-light photo lights up every column, reads as a
    # boundary everywhere, and silently disables the merging below.
    median = float(np.median(column_hits))
    return peak >= median * VERTICAL_DIVIDER_PEAK_RATIO


# --- passes ---------------------------------------------------------------


def _same_spine(a: Box, b: Box, gray: np.ndarray) -> bool:
    """Whether two boxes are two attempts at the *same* physical spine."""
    if has_vertical_divider(gray, a, b):
        return False
    if iou(a, b) >= DEDUP_IOU_THRESHOLD:
        return True
    # Contained-but-much-narrower means the wide box spans more than one book;
    # keeping both is the safe read, since dropping one loses a real title.
    return containment(a, b) >= DEDUP_CONTAINMENT_THRESHOLD and width_ratio(a, b) >= DEDUP_MIN_WIDTH_RATIO


def deduplicate_boxes(scored: list[tuple[Box, float]], gray: np.ndarray) -> list[Box]:
    """
    Greedy merge in confidence order. A box that matches one already kept is
    absorbed into it (union, so a fragment grows the box to the whole spine)
    rather than simply discarded.
    """
    kept: list[tuple[Box, float]] = []
    for box, confidence in sorted(scored, key=lambda pair: pair[1], reverse=True):
        for index, (kept_box, kept_confidence) in enumerate(kept):
            if _same_spine(box, kept_box, gray):
                kept[index] = (union_box(kept_box, box), max(kept_confidence, confidence))
                break
        else:
            kept.append((box, confidence))
    return [box for box, _ in kept]


def detect_book_boxes(image: Image.Image) -> list[Box]:
    """Returns (x1, y1, x2, y2) pixel boxes, one per book spine, left to right."""
    model = _get_model()
    book_class_id = next((k for k, v in model.names.items() if v == YOLO_BOOK_CLASS_NAME), None)
    if book_class_id is None:
        return []

    results = model.predict(
        image,
        verbose=False,
        conf=YOLO_CONFIDENCE_THRESHOLD,
        iou=YOLO_NMS_IOU_THRESHOLD,
    )

    scored: list[tuple[Box, float]] = []
    for box in results[0].boxes:
        if int(box.cls[0]) != book_class_id:
            continue
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        scored.append(((int(x1), int(y1), int(x2), int(y2)), float(box.conf[0])))

    if not scored:
        return []
    raw_count = len(scored)

    # Pass 1: shape. Drops cartons, shelves and background clutter.
    shaped = [pair for pair in scored if is_spine_shaped(pair[0])]
    # A shelf shot of books lying flat has no tall-narrow box in it. Rather
    # than report an empty shelf, fall back to the unfiltered set and let the
    # VLM sort it out.
    if not shaped:
        logger.debug("Spine-shape filter matched nothing; keeping all %d raw boxes", raw_count)
        shaped = scored

    # Pass 2: slivers too thin to be a whole spine.
    min_width = image.width * MIN_SPINE_WIDTH_RATIO
    wide_enough = [pair for pair in shaped if (pair[0][2] - pair[0][0]) >= min_width]
    if not wide_enough:
        wide_enough = shaped

    # Pass 3: merge boxes that are the same spine seen twice.
    gray = np.asarray(image.convert("L"))
    boxes = deduplicate_boxes(wide_enough, gray)

    if len(boxes) != raw_count:
        logger.debug(
            "Spine boxes: %d raw -> %d shaped -> %d wide enough -> %d after dedup",
            raw_count,
            len(shaped),
            len(wide_enough),
            len(boxes),
        )
    return sorted(boxes, key=lambda b: b[0])
