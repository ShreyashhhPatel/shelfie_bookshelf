"""
Exercises every node of the scan pipeline in isolation against a real photo,
so a failure points at one stage instead of a generic 500.

Unlike the pytest suite (which mocks the VLM), this runs the real thing
end to end: convert -> detect -> crop -> read -> match. Node 4 makes a
billed Gemini call; pass --skip-vlm to check the surrounding nodes for free.

    python scripts/pipeline_check.py [--photo PATH] [--skip-vlm]
"""

import argparse
import os
import sys
import time
import traceback
from io import BytesIO
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "shelfie.settings")

import django  # noqa: E402

django.setup()

from scanner.constants import CROP_PADDING_PX  # noqa: E402
from scanner.models import CatalogBook  # noqa: E402
from scanner.services import image_utils, matcher, vlm_read, yolo_detect  # noqa: E402

DEFAULT_PHOTO = BACKEND_DIR / "test_photos" / "shelf_2.heic"

PASS, WARN, SKIP, FAIL = "PASS", "WARN", "SKIP", "FAIL"
COLORS = {PASS: "\033[32m", WARN: "\033[33m", SKIP: "\033[90m", FAIL: "\033[31m"}
RESET = "\033[0m"


class Report:
    """Collects one line per node so the summary reads as a checklist."""

    def __init__(self):
        self.rows: list[tuple[int, str, str, str, int]] = []
        self.failed = False

    def add(self, node: int, name: str, status: str, detail: str, ms: int) -> None:
        if status == FAIL:
            self.failed = True
        self.rows.append((node, name, status, detail, ms))
        color = COLORS[status] if sys.stdout.isatty() else ""
        reset = RESET if sys.stdout.isatty() else ""
        print(f"  node {node}  {name:<10} {color}{status:<4}{reset}  {detail:<44} {ms:>6} ms")


class Stage:
    """Times a node and turns an unexpected exception into a FAIL row."""

    def __init__(self, report: Report, node: int, name: str):
        self.report, self.node, self.name = report, node, name

    def __enter__(self):
        self.start = time.monotonic()
        return self

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.start) * 1000)

    def done(self, status: str, detail: str) -> None:
        self.report.add(self.node, self.name, status, detail, self.elapsed_ms())

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            return False
        self.report.add(self.node, self.name, FAIL, f"{exc_type.__name__}: {exc}"[:44], self.elapsed_ms())
        traceback.print_exception(exc_type, exc, tb, limit=3)
        return True  # swallow: later nodes can't run, main() exits on report.failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Check each node of the scan pipeline.")
    parser.add_argument("--photo", type=Path, default=DEFAULT_PHOTO, help="shelf image to run through")
    parser.add_argument("--skip-vlm", action="store_true", help="skip the billed Gemini call")
    args = parser.parse_args()

    if not args.photo.exists():
        print(f"photo not found: {args.photo}")
        return 1

    print(f"\npipeline check  photo={args.photo.name}  vlm={'skipped' if args.skip_vlm else 'live'}\n")
    report = Report()

    # Node 0 -- the catalog every match is scored against.
    with Stage(report, 0, "catalog") as stage:
        count = CatalogBook.objects.count()
        if count == 0:
            stage.done(FAIL, "empty -- run: manage.py load_catalog")
        else:
            stage.done(PASS, f"{count} books loaded")
    if report.failed:
        return finish(report)

    # Node 1 -- decode anything (HEIC included) and re-encode as JPEG.
    with Stage(report, 1, "convert") as stage:
        raw = args.photo.read_bytes()
        jpeg_bytes = image_utils.to_jpeg_bytes(BytesIO(raw))
        image = image_utils.load_rgb_image(jpeg_bytes)
        stage.done(PASS, f"{len(raw) // 1024}KB {args.photo.suffix.lstrip('.')} -> {len(jpeg_bytes) // 1024}KB jpeg {image.size[0]}x{image.size[1]}")
    if report.failed:
        return finish(report)

    # Node 2 -- YOLO spine boxes. Zero boxes is a real path, not a crash:
    # the pipeline falls back to reading the whole image.
    with Stage(report, 2, "detect") as stage:
        boxes = yolo_detect.detect_book_boxes(image)
        if boxes:
            stage.done(PASS, f"{len(boxes)} spine boxes")
        else:
            stage.done(WARN, "0 boxes -- full-image fallback path")
    if report.failed:
        return finish(report)

    # Node 3 -- pad, crop, and rotate vertical spine text upright.
    crop_bytes_list: list[bytes] = []
    with Stage(report, 3, "crop") as stage:
        if not boxes:
            stage.done(SKIP, "no boxes to crop (fallback)")
        else:
            rotated = 0
            for box in boxes:
                crop = image_utils.crop_with_padding(image, box, CROP_PADDING_PX)
                upright = image_utils.rotate_if_tall_narrow(crop)
                rotated += upright.size != crop.size
                crop_bytes_list.append(image_utils.image_to_jpeg_bytes(upright))
            stage.done(PASS, f"{len(crop_bytes_list)} crops, {rotated} rotated upright")
    if report.failed:
        return finish(report)

    # Node 4 -- the VLM read. Billed, so --skip-vlm substitutes a fixture
    # read that still gives node 5 something real to score.
    reads: list[dict] = []
    with Stage(report, 4, "vlm read") as stage:
        if args.skip_vlm:
            reads = [{"title": "Dune", "author": "Frank Herbert", "readable": True, "status": "ok"}]
            stage.done(SKIP, "--skip-vlm, using fixture read")
        elif boxes:
            reads = vlm_read.read_spines_batch(crop_bytes_list)
            ok = sum(r.get("status") == "ok" for r in reads)
            dupes = sum(r.get("status") == "duplicate" for r in reads)
            status = PASS if ok else WARN
            stage.done(status, f"{ok}/{len(reads)} readable, {dupes} duplicate")
        else:
            reads = vlm_read.read_full_image_fallback(jpeg_bytes)
            stage.done(PASS if reads else WARN, f"fallback returned {len(reads)} titles")
    if report.failed:
        return finish(report)

    # Node 5 -- score each read against the catalog and bucket it.
    with Stage(report, 5, "match") as stage:
        catalog = list(CatalogBook.objects.all().values("id", "title", "author", "alt_titles"))
        buckets = {matcher.STATUS_AUTO_ADDED: 0, matcher.STATUS_NEEDS_REVIEW: 0, matcher.STATUS_UNMATCHED: 0}
        matched = []
        for read in reads:
            if not read.get("title"):
                continue
            result = matcher.match_book(read.get("title"), read.get("author"), catalog)
            buckets[result.status] += 1
            matched.append((read, result))
        if not matched:
            stage.done(WARN, "no readable titles to score")
        else:
            stage.done(
                PASS,
                f"{len(matched)} scored -- {buckets[matcher.STATUS_AUTO_ADDED]} auto, "
                f"{buckets[matcher.STATUS_NEEDS_REVIEW]} review, {buckets[matcher.STATUS_UNMATCHED]} unmatched",
            )

    if matched:
        print("\n  reads:")
        for read, result in matched:
            top = result.candidates[0].book["title"] if result.candidates else "-"
            conf = f"{result.confidence:.2f}" if result.confidence is not None else "-"
            print(f"    {read.get('title')!r} / {read.get('author')!r}")
            print(f"      -> {result.status}  conf={conf}  top candidate: {top}")

    return finish(report)


def finish(report: Report) -> int:
    total = sum(row[4] for row in report.rows)
    if report.failed:
        broken = next(row for row in report.rows if row[2] == FAIL)
        print(f"\nFAILED at node {broken[0]} ({broken[1]}) -- {total} ms\n")
        return 1
    print(f"\nall nodes healthy -- {total} ms\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
