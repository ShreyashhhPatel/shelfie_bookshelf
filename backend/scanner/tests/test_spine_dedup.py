"""
Over-counting control for YOLO spine boxes.

The regression these guard against is measured, not hypothetical: on
test_photos/shelf_2, raw YOLO returns 9 boxes for a 3-book shelf (a cardboard
carton plus blurry background clutter), and on shelf_1 it draws a wide box
across two touching books as well as a thin box on the second one.

The dangerous direction is losing a book, not keeping a spare box, so that
case gets the most coverage here: two touching books must never collapse into
one row, however much their boxes overlap.
"""

from unittest.mock import patch

import numpy as np
from PIL import Image

from scanner.services import yolo_detect
from scanner.services.yolo_detect import (
    containment,
    deduplicate_boxes,
    has_vertical_divider,
    intersection_area,
    iou,
    is_spine_shaped,
    union_box,
    width_ratio,
)

# Uniform gray: Canny finds no edges, so the divider check is out of the way
# and the box arithmetic is what's under test.
BLANK = np.full((6000, 5000), 128, dtype=np.uint8)


# ---------- geometry ----------


def test_iou_and_containment_disagree_on_a_nested_box():
    """
    Why containment exists. A title-sized box inside a full-spine box is the
    single most common duplicate, and IoU alone scores it far too low to act
    on -- these are the real coordinates from shelf_1.
    """
    wide = (1290, 1332, 2153, 5401)
    thin = (1884, 1716, 2168, 5554)

    assert iou(wide, thin) < 0.30
    assert containment(wide, thin) > 0.90


def test_disjoint_boxes_score_zero():
    a, b = (0, 0, 100, 500), (200, 0, 300, 500)
    assert intersection_area(a, b) == 0
    assert iou(a, b) == 0.0
    assert containment(a, b) == 0.0


def test_zero_area_box_does_not_divide_by_zero():
    assert containment((10, 10, 10, 10), (0, 0, 100, 100)) == 0.0
    assert iou((10, 10, 10, 10), (10, 10, 10, 10)) == 0.0
    assert width_ratio((10, 10, 10, 500), (10, 10, 10, 500)) == 0.0


def test_union_box_covers_both():
    assert union_box((10, 20, 30, 40), (25, 5, 60, 35)) == (10, 5, 60, 40)


def test_width_ratio_is_orientation_independent():
    wide, thin = (0, 0, 300, 1000), (0, 0, 100, 1000)
    assert width_ratio(wide, thin) == width_ratio(thin, wide)
    assert width_ratio(wide, thin) == 1 / 3


def test_spine_shape_filter_separates_spines_from_cartons():
    assert is_spine_shaped((1502, 645, 2015, 4716))  # real spine, h/w ~7.9
    assert not is_spine_shaped((2857, 1540, 4280, 4349))  # carton, h/w ~2.0
    assert not is_spine_shaped((2, 2392, 1572, 2871))  # clutter, wider than tall
    assert not is_spine_shaped((10, 10, 10, 500))  # zero width


# ---------- the merge decision ----------


def test_two_boxes_on_one_spine_merge():
    """A spine found twice, the boxes offset slightly: one row, not two."""
    boxes = [((100, 100, 300, 2000), 0.9), ((110, 120, 305, 2010), 0.7)]
    assert len(deduplicate_boxes(boxes, BLANK)) == 1


def test_fragment_inside_a_spine_merges_and_grows_to_the_whole_spine():
    """
    A title-block box inside its own spine box. Absorbing it must not shrink
    the survivor -- the VLM needs the whole spine to read author and title.
    """
    spine = (100, 100, 300, 2000)
    fragment = (120, 400, 290, 900)
    kept = deduplicate_boxes([(spine, 0.9), (fragment, 0.8)], BLANK)
    assert kept == [spine]


def test_touching_books_are_kept_apart_when_the_swallowed_box_is_narrow():
    """
    The shelf_1 regression, and the one that actually loses a book: a wide box
    spanning two touching spines, plus a thin box on the second spine. 91%
    contained, so containment alone would drop 'The Art of War'. The width
    guard is what keeps it.
    """
    wide = (1290, 1332, 2153, 5401)
    thin = (1884, 1716, 2168, 5554)

    assert containment(wide, thin) > 0.80  # a naive rule would merge these
    assert width_ratio(wide, thin) < 0.60  # ...and this is why it must not

    kept = deduplicate_boxes([(wide, 0.9), (thin, 0.6)], BLANK)
    assert len(kept) == 2


def test_a_vertical_edge_between_two_boxes_blocks_the_merge():
    """
    Boxes overlapping enough to merge on IoU alone, but with a spine boundary
    running down the overlap, stay separate.
    """
    gray = np.full((2000, 800), 200, dtype=np.uint8)
    gray[:, 395:405] = 10  # the shadow line where two spines meet

    a, b = (300, 0, 500, 2000), (310, 10, 510, 1990)
    assert iou(a, b) > 0.55  # would merge without the pixel check
    assert has_vertical_divider(gray, a, b)
    assert len(deduplicate_boxes([(a, 0.9), (b, 0.8)], gray)) == 2


def test_divider_check_ignores_texture_that_is_not_a_full_height_edge():
    """Cover art and title text must not read as a spine boundary."""
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, size=(2000, 800), dtype=np.uint8)
    assert not has_vertical_divider(noisy, (300, 0, 500, 2000), (310, 10, 510, 1990))


def test_divider_check_tolerates_a_degenerate_overlap():
    assert not has_vertical_divider(BLANK, (0, 0, 100, 100), (500, 500, 600, 600))


# ---------- detect_book_boxes end to end (model mocked) ----------


class _FakeBox:
    def __init__(self, xyxy, conf, cls=0):
        self.xyxy = [np.array(xyxy, dtype=float)]
        self.conf = [conf]
        self.cls = [cls]


class _FakeModel:
    names = {0: "book", 1: "vase"}

    def __init__(self, boxes):
        self._boxes = boxes

    def predict(self, *args, **kwargs):
        return [type("R", (), {"boxes": self._boxes})()]


def _detect(fake_boxes, size=(5000, 6000)):
    with patch.object(yolo_detect, "_get_model", return_value=_FakeModel(fake_boxes)):
        return yolo_detect.detect_book_boxes(Image.new("RGB", size, color=(128, 128, 128)))


def test_non_book_classes_are_ignored():
    boxes = _detect([_FakeBox([100, 100, 300, 2000], 0.9, cls=0), _FakeBox([400, 100, 600, 2000], 0.9, cls=1)])
    assert boxes == [(100, 100, 300, 2000)]


def test_squat_boxes_are_dropped_before_the_vlm_sees_them():
    """The carton and the background clutter never become billable crops."""
    boxes = _detect(
        [
            _FakeBox([100, 100, 300, 2000], 0.9),  # spine
            _FakeBox([2857, 1540, 4280, 4349], 0.9),  # carton
            _FakeBox([2, 2392, 1572, 2871], 0.9),  # clutter
        ]
    )
    assert boxes == [(100, 100, 300, 2000)]


def test_shape_filter_falls_back_rather_than_reporting_an_empty_shelf():
    """Books lying flat have no tall-narrow box; better to over-offer than to
    tell the user their shelf is empty."""
    flat = [_FakeBox([100, 100, 2000, 600], 0.9), _FakeBox([100, 700, 2000, 1200], 0.9)]
    assert len(_detect(flat)) == 2


def test_slivers_narrower_than_a_spine_are_dropped():
    # 5000px-wide image -> anything under 75px is a sliver.
    boxes = _detect([_FakeBox([100, 100, 300, 2000], 0.9), _FakeBox([900, 100, 940, 2000], 0.9)])
    assert boxes == [(100, 100, 300, 2000)]


def test_no_detections_returns_empty():
    assert _detect([]) == []


def test_boxes_come_back_left_to_right():
    boxes = _detect(
        [
            _FakeBox([2000, 100, 2300, 2000], 0.9),
            _FakeBox([100, 100, 400, 2000], 0.9),
            _FakeBox([1000, 100, 1300, 2000], 0.9),
        ]
    )
    assert [b[0] for b in boxes] == [100, 1000, 2000]
