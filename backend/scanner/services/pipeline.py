"""
Orchestrates one scan end to end: convert -> detect -> crop/rotate -> batch
VLM read -> match -> persist Detections. Synchronous by design -- see the
"Honest caveat" in CONTEXT.md's data model section: a real product with
thousands of users would push this onto a background queue (Celery +
polling) instead of a synchronous request.
"""

import time
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from scanner.constants import CROP_PADDING_PX
from scanner.models import CatalogBook, Detection, LibraryEntry, Scan
from scanner.services import image_utils, matcher, vlm_read, yolo_detect


class StageTimer:
    """Records per-stage duration in ms, plus a running total, for the
    latency numbers CONTEXT.md wants in the README."""

    def __init__(self):
        self._start = time.monotonic()
        self._last = self._start
        self.marks: dict[str, int] = {}

    def mark(self, name: str) -> None:
        now = time.monotonic()
        self.marks[name] = round((now - self._last) * 1000)
        self._last = now

    def finalize(self) -> dict[str, int]:
        self.marks["total_ms"] = round((time.monotonic() - self._start) * 1000)
        return self.marks


def _catalog_as_dicts() -> list[dict]:
    return list(
        CatalogBook.objects.all().values(
            "id", "title", "author", "alt_titles", "year", "series", "spine_color", "spine_hex"
        )
    )


def _candidates_payload(result: matcher.MatchResult) -> list[dict]:
    return [
        {
            "book_id": c.book["id"],
            "title": c.book["title"],
            "author": c.book["author"],
            "score": round(c.score, 4),
            # Year and series are what actually separate two candidates the
            # matcher has tied: the 2016 Alderman "The Power" from the 2010
            # Byrne one, or the two editions of Fahrenheit 451. Without them
            # the review card shows two identical rows and the user cannot
            # resolve the very ambiguities the catalog plants on purpose.
            "year": c.book.get("year"),
            "series": c.book.get("series", ""),
            # Colour settles a near-tie by glancing at the shelf.
            "spine_color": c.book.get("spine_color", ""),
            "spine_hex": c.book.get("spine_hex", ""),
        }
        for c in result.candidates
    ]


def apply_read_to_detection(detection: Detection, read: dict, catalog: list[dict]) -> Detection:
    """Scores one VLM read against the catalog and updates `detection` in place (caller saves)."""
    detection.read_title = read.get("title")
    detection.read_author = read.get("author")
    detection.read_spine_color = read.get("spine_color")

    # Another crop of a book we already have a row for (the detector draws
    # overlapping boxes around one spine). The row is kept rather than dropped
    # -- nothing in this pipeline disappears silently -- but it leaves the
    # review queue, so the user isn't asked about the same book three times.
    # Not a book at all -- a pillar, a carton, furniture the detector boxed.
    # Discarded rather than queued: there is nothing for a person to identify,
    # and leaving it in review buries the blurry spines that do need them.
    if read.get("status") == "not_a_book":
        detection.status = Detection.STATUS_DISCARDED
        detection.confidence = None
        detection.margin = None
        detection.candidates = []
        detection.chosen_book = None
        return detection

    if read.get("status") == "duplicate":
        detection.status = Detection.STATUS_DISCARDED
        detection.confidence = None
        detection.margin = None
        detection.candidates = []
        detection.chosen_book = None
        return detection

    # Full-image-fallback reads have no "readable" key at all (see
    # vlm_read.read_full_image_fallback) -- absence means "readable",
    # unlike the batched-crop reads where it's always explicit.
    if not read.get("readable", True) or not read.get("title"):
        detection.status = Detection.STATUS_FAILED if read.get("status") == "failed" else Detection.STATUS_UNREADABLE
        detection.confidence = None
        detection.margin = None
        detection.candidates = []
        detection.chosen_book = None
        return detection

    result = matcher.match_book(read.get("title"), read.get("author"), catalog)
    result = matcher.apply_color_tiebreak(result, read.get("spine_color"))
    detection.status = result.status
    detection.confidence = result.confidence
    detection.margin = result.margin
    detection.candidates = _candidates_payload(result)
    detection.chosen_book_id = (
        result.candidates[0].book["id"]
        if result.status == matcher.STATUS_AUTO_ADDED and result.candidates
        else None
    )
    return detection


def auto_confirm_if_high_confidence(detection: Detection) -> None:
    """
    High-confidence matches auto-add straight to the library -- no human
    step required. Everything else waits for a review-screen confirm/correct
    action, so only ever-confirmed detections reach the library list.
    """
    if detection.status != Detection.STATUS_AUTO_ADDED:
        return
    LibraryEntry.objects.create(book_id=detection.chosen_book_id, source_detection=detection)


def _create_detection(scan: Scan, box: tuple[int, int, int, int], crop_bytes: bytes, read: dict, catalog: list[dict]) -> Detection:
    x1, y1, x2, y2 = box
    detection = Detection(scan=scan, bbox={"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    apply_read_to_detection(detection, read, catalog)
    detection.crop.save(f"crop_{scan.id}_{x1}_{y1}.jpg", ContentFile(crop_bytes), save=False)
    detection.save()
    auto_confirm_if_high_confidence(detection)
    return detection


def _create_detection_fullimage(scan: Scan, read: dict, catalog: list[dict]) -> Detection:
    detection = Detection(scan=scan, bbox={})
    apply_read_to_detection(detection, read, catalog)
    detection.save()
    auto_confirm_if_high_confidence(detection)
    return detection


def run_pipeline(scan: Scan) -> Scan:
    timer = StageTimer()
    scan.status = Scan.STATUS_PROCESSING
    scan.save(update_fields=["status"])

    scan.image.open("rb")
    original_name = scan.image.name
    jpeg_bytes = image_utils.to_jpeg_bytes(BytesIO(scan.image.read()))
    timer.mark("convert_ms")

    # Persist the normalized JPEG so `image_url` is always something a client
    # can actually render. iPhones shoot HEIC, and the web picker hands the
    # file through untouched (Chrome's canvas can't decode HEIC to re-encode
    # it), so without this the results screen shows a broken hero image.
    if not original_name.lower().endswith((".jpg", ".jpeg")):
        stem = Path(original_name).stem
        scan.image.save(f"{stem}.jpg", ContentFile(jpeg_bytes), save=True)
        default_storage.delete(original_name)

    image = image_utils.load_rgb_image(jpeg_bytes)
    boxes = yolo_detect.detect_book_boxes(image)
    timer.mark("detect_ms")

    catalog = _catalog_as_dicts()

    if not boxes:
        scan.used_full_image_fallback = True
        reads = vlm_read.read_full_image_fallback(jpeg_bytes)
        timer.mark("vlm_ms")

        if not reads:
            scan.empty_result = True
            scan.status = Scan.STATUS_DONE
            scan.timings = timer.finalize()
            scan.save(update_fields=["status", "timings", "used_full_image_fallback", "empty_result"])
            return scan

        for read in reads:
            _create_detection_fullimage(scan, read, catalog)
        timer.mark("match_ms")

        scan.status = Scan.STATUS_DONE
        scan.timings = timer.finalize()
        scan.save(update_fields=["status", "timings", "used_full_image_fallback"])
        return scan

    crops = []
    for box in boxes:
        crop = image_utils.crop_with_padding(image, box, CROP_PADDING_PX)
        crop = image_utils.rotate_if_tall_narrow(crop)
        crops.append(crop)
    crop_bytes_list = [image_utils.image_to_jpeg_bytes(c) for c in crops]
    timer.mark("crop_ms")

    reads = vlm_read.read_spines_batch(crop_bytes_list)
    timer.mark("vlm_ms")

    for box, crop_bytes, read in zip(boxes, crop_bytes_list, reads):
        _create_detection(scan, box, crop_bytes, read, catalog)
    timer.mark("match_ms")

    scan.status = Scan.STATUS_DONE
    scan.timings = timer.finalize()
    scan.save(update_fields=["status", "timings", "used_full_image_fallback"])
    return scan
