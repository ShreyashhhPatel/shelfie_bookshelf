import io
from unittest.mock import patch

import pillow_heif
import pytest
from PIL import Image

from scanner.models import CatalogBook, Detection, LibraryEntry, Scan
from scanner.services import image_utils, vlm_read
from scanner.services.pipeline import run_pipeline
from scanner.services.vlm_read import VLMCallError

pytestmark = pytest.mark.django_db


def _jpeg_bytes(size=(200, 400), color=(30, 60, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_scan(image_bytes=None):
    from django.core.files.base import ContentFile

    scan = Scan()
    scan.image.save("shelf.jpg", ContentFile(image_bytes or _jpeg_bytes()), save=True)
    return scan


@pytest.fixture
def dune_catalog(db):
    CatalogBook.objects.create(title="Dune", author="Frank Herbert", alt_titles=[])


# ---------- HEIC / odd formats ----------


def test_heic_upload_converts_to_jpeg():
    heif_file = pillow_heif.from_pillow(Image.new("RGB", (64, 96), color=(200, 50, 50)))
    buf = io.BytesIO()
    heif_file.save(buf, quality=90)
    heic_bytes = buf.getvalue()

    jpeg_bytes = image_utils.to_jpeg_bytes(io.BytesIO(heic_bytes))

    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes
    decoded = Image.open(io.BytesIO(jpeg_bytes))
    assert decoded.format == "JPEG"
    assert decoded.size == (64, 96)


# ---------- zero books detected -> full-image fallback ----------


def test_zero_detections_falls_back_to_full_image_call(dune_catalog):
    scan = _make_scan()
    with (
        patch("scanner.services.pipeline.yolo_detect.detect_book_boxes", return_value=[]),
        patch(
            "scanner.services.pipeline.vlm_read.read_full_image_fallback",
            return_value=[{"title": "Dune", "author": "Frank Herbert"}],
        ) as mock_fallback,
    ):
        run_pipeline(scan)

    mock_fallback.assert_called_once()
    scan.refresh_from_db()
    assert scan.status == Scan.STATUS_DONE
    assert scan.used_full_image_fallback is True
    assert scan.empty_result is False
    assert scan.detections.count() == 1
    detection = scan.detections.first()
    assert detection.status == Detection.STATUS_AUTO_ADDED
    # High-confidence matches auto-add straight to the library, no human step.
    assert LibraryEntry.objects.filter(source_detection=detection).exists()


def test_needs_review_detection_does_not_reach_library_until_confirmed(dune_catalog):
    scan = _make_scan()
    with (
        patch("scanner.services.pipeline.yolo_detect.detect_book_boxes", return_value=[(0, 0, 50, 200)]),
        patch(
            "scanner.services.pipeline.vlm_read.read_spines_batch",
            # No author read -> confidence capped below the auto-accept score threshold -> needs_review.
            return_value=[{"index": 1, "title": "Dune", "author": None, "readable": True, "status": "ok"}],
        ),
    ):
        run_pipeline(scan)

    scan.refresh_from_db()
    detection = scan.detections.first()
    assert detection.status == Detection.STATUS_NEEDS_REVIEW
    assert LibraryEntry.objects.filter(source_detection=detection).exists() is False


def test_zero_detections_and_empty_fallback_yields_friendly_empty_state():
    scan = _make_scan()
    with (
        patch("scanner.services.pipeline.yolo_detect.detect_book_boxes", return_value=[]),
        patch("scanner.services.pipeline.vlm_read.read_full_image_fallback", return_value=[]),
    ):
        run_pipeline(scan)

    scan.refresh_from_db()
    assert scan.status == Scan.STATUS_DONE
    assert scan.empty_result is True
    assert scan.detections.count() == 0


# ---------- unreadable spine: never silently dropped ----------


def test_unreadable_spine_becomes_a_review_card_not_a_drop(dune_catalog):
    scan = _make_scan()
    with (
        patch("scanner.services.pipeline.yolo_detect.detect_book_boxes", return_value=[(0, 0, 50, 200)]),
        patch(
            "scanner.services.pipeline.vlm_read.read_spines_batch",
            return_value=[{"index": 1, "title": None, "author": None, "readable": False, "status": "unreadable"}],
        ),
    ):
        run_pipeline(scan)

    scan.refresh_from_db()
    assert scan.detections.count() == 1
    detection = scan.detections.first()
    assert detection.status == Detection.STATUS_UNREADABLE
    assert detection.read_title is None


# ---------- VLM timeout / API error -> retry then failed, never a crash ----------


def test_vlm_call_retries_once_then_returns_failed_status_after_persistent_error():
    with patch("scanner.services.vlm_read._get_client") as mock_get_client, patch("time.sleep"):
        mock_client = mock_get_client.return_value
        mock_client.models.generate_content.side_effect = TimeoutError("upstream timed out")

        results = vlm_read.read_spines_batch([_jpeg_bytes()])

    assert mock_client.models.generate_content.call_count == 2  # one call + one retry
    assert results == [{"index": 1, "title": None, "author": None, "readable": False, "status": "failed"}]


def test_vlm_call_succeeds_on_retry_after_one_transient_failure():
    class FakeResponse:
        text = '[{"index": 1, "title": "Dune", "author": "Frank Herbert", "readable": true}]'

    with patch("scanner.services.vlm_read._get_client") as mock_get_client, patch("time.sleep"):
        mock_client = mock_get_client.return_value
        mock_client.models.generate_content.side_effect = [TimeoutError("flaky"), FakeResponse()]

        results = vlm_read.read_spines_batch([_jpeg_bytes()])

    assert results == [
        {
            "index": 1,
            "title": "Dune",
            "author": "Frank Herbert",
            # A model that omits the colour still yields a well-formed read.
            "spine_color": None,
            "readable": True,
            "status": "ok",
        }
    ]


def test_pipeline_marks_detection_failed_when_vlm_permanently_errors(dune_catalog):
    scan = _make_scan()
    with (
        patch("scanner.services.pipeline.yolo_detect.detect_book_boxes", return_value=[(0, 0, 50, 200)]),
        patch(
            "scanner.services.pipeline.vlm_read.read_spines_batch",
            return_value=[{"index": 1, "title": None, "author": None, "readable": False, "status": "failed"}],
        ),
    ):
        run_pipeline(scan)

    scan.refresh_from_db()
    detection = scan.detections.first()
    assert detection.status == Detection.STATUS_FAILED


# ---------- malformed JSON -> one repair retry, then routed to review as unreadable ----------


def test_malformed_json_triggers_one_repair_retry_then_succeeds():
    class Resp:
        def __init__(self, text):
            self.text = text

    responses = [
        Resp("Sure, here you go: not actually json"),
        Resp('[{"index": 1, "title": "Dune", "author": "Frank Herbert", "readable": true}]'),
    ]
    with patch("scanner.services.vlm_read._get_client") as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.models.generate_content.side_effect = responses

        results = vlm_read.read_spines_batch([_jpeg_bytes()])

    assert mock_client.models.generate_content.call_count == 2
    assert results[0]["title"] == "Dune"


def test_malformed_json_persists_after_repair_retry_becomes_unreadable():
    class Resp:
        def __init__(self, text):
            self.text = text

    with patch("scanner.services.vlm_read._get_client") as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.models.generate_content.side_effect = [Resp("still not json"), Resp("nope, still not json")]

        results = vlm_read.read_spines_batch([_jpeg_bytes()])

    assert results == [{"index": 1, "title": None, "author": None, "readable": False, "status": "unreadable"}]
