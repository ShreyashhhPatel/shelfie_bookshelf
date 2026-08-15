"""The whole scan, start to finish.

Photo in, Detection rows out. This is the only module that knows the order of
the stages, and the only one that touches the database during a scan -- the
services under it are all pure enough to run on a bare image.

It runs synchronously inside the request. That is a deliberate limitation of
this phase and the first thing that should move: the read stage alone is
seconds of network wait, and SQLite serializes writes behind it.
"""

import logging
import time
from pathlib import Path
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.db import transaction

from ..models import Detection, Scan
from .image_utils import load_image, prepare_crop, to_jpeg_bytes
from .matcher import catalog_entries, match
from .vlm_read import ReadErrorCode, VlmReadError, read_spines
from .yolo_detect import SpineDetection, detect_book_boxes

logger = logging.getLogger(__name__)


class StageTimer:
    """Collects wall-clock milliseconds per stage into a plain dict.

    Wall clock rather than CPU time on purpose: the stage that matters most is
    the one waiting on a network round trip, which costs no CPU at all.
    """

    def __init__(self) -> None:
        self.timings: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            # Recorded even when the stage raised. A failed scan's timings are
            # the most useful ones -- they say how far it got and where.
            self.timings[name] = elapsed_ms
            logger.info('stage %s took %dms', name, elapsed_ms)

    @property
    def total_ms(self) -> int:
        return sum(self.timings.values())


def run_scan(scan: Scan) -> Scan:
    """Run the full pipeline for one uploaded photo.

    Marks the scan failed and re-raises nothing: a failure is a state on the
    record, not an exception the view has to catch. The client polls or reads
    the response either way.
    """
    timer = StageTimer()

    try:
        with timer.stage('decode'):
            scan.image.open('rb')
            try:
                image = load_image(scan.image.read())
            finally:
                scan.image.close()
            # An iPhone upload is HEIC, which no browser renders and which the
            # results screen would show as a broken image. The decoded frame is
            # written back as JPEG so `image_url` is always displayable, and so
            # the stored bytes match what the detector actually saw.
            _normalize_stored_image(scan, image)

        _set_status(scan, Scan.Status.DETECTING)
        with timer.stage('detect'):
            spines = detect_book_boxes(image)

        # A close-up of one or two books, or an angle the detector dislikes,
        # returns nothing. Reading the whole frame is a far better answer than
        # an empty result: the model can find a title in it, and the worst case
        # is one unreadable detection the user discards.
        whole_image_fallback = not spines
        if whole_image_fallback:
            logger.info('Scan %s: no spines detected, reading the whole image', scan.pk)
            width, height = image.size
            spines = [SpineDetection(box=(0, 0, width, height), confidence=0.0)]

        with timer.stage('crop'):
            crops = (
                # No cropping or rotating on the fallback path -- there is no
                # box to pad and the frame is already the right way up.
                [to_jpeg_bytes(image)]
                if whole_image_fallback
                else [prepare_crop(image, spine.box) for spine in spines]
            )

        _set_status(scan, Scan.Status.READING)
        with timer.stage('read'):
            reads = read_spines(crops)

        _set_status(scan, Scan.Status.MATCHING)
        with timer.stage('match'):
            # Loaded once for the whole photo, not once per spine.
            entries = catalog_entries()
            results = [match(read.title, read.author, entries) for read in reads]

        with timer.stage('persist'):
            _persist(scan, spines, crops, reads, results)

        scan.timings = timer.timings
        _set_status(scan, Scan.Status.COMPLETE, timings=True)
        logger.info(
            'Scan %s complete: %d spine(s) in %dms', scan.pk, len(spines), timer.total_ms
        )

    except VlmReadError as cause:
        scan.timings = timer.timings
        # The user gets the sentence; the provider's raw text stays in the log.
        scan.error = cause.user_message
        scan.error_code = cause.code.value
        _set_status(scan, Scan.Status.FAILED, timings=True, error=True)
        logger.warning(
            'Scan %s failed during read (%s): %s', scan.pk, cause.code.value, cause.detail
        )

    except Exception as cause:  # noqa: BLE001 - the record must carry the failure
        scan.timings = timer.timings
        # Nothing below the read stage has a user-facing vocabulary yet, so
        # anything reaching here is genuinely unexpected and says so plainly
        # rather than leaking a traceback into the UI.
        scan.error = 'Something went wrong processing this photo.'
        scan.error_code = ReadErrorCode.UNKNOWN.value
        _set_status(scan, Scan.Status.FAILED, timings=True, error=True)
        logger.exception('Scan %s failed', scan.pk)

    return scan


def _set_status(scan: Scan, status: str, timings: bool = False, error: bool = False):
    """Narrow update so a long pipeline does not clobber concurrent writes."""
    scan.status = status
    fields = ['status', 'updated_at']
    if timings:
        fields.append('timings')
    if error:
        fields.extend(['error', 'error_code'])
    scan.save(update_fields=fields)


@transaction.atomic
def _persist(scan, spines, crops, reads, results) -> list[Detection]:
    """Write one Detection per spine.

    Atomic so a photo is all-or-nothing: a half-written scan would show the
    user a review screen missing books they can see in the photo.
    """
    detections: list[Detection] = []
    by_index: dict[int, Detection] = {}

    for spine, crop_bytes, read, result in zip(spines, crops, reads, results):
        best = result.best

        if read.is_empty:
            # Nothing was read, so there is nothing to match. Distinct from a
            # read that matched badly, and the review screen says so.
            status = Detection.Status.NEEDS_REVIEW
        elif result.should_auto_accept:
            status = Detection.Status.AUTO_MATCHED
        else:
            status = Detection.Status.NEEDS_REVIEW

        detection = Detection(
            scan=scan,
            bbox=list(spine.box),
            confidence=spine.confidence,
            raw_title=read.title,
            raw_author=read.author,
            candidates=result.as_dicts(),
            margin=result.margin,
            status=status,
        )
        if best is not None:
            # Set whether or not it auto-matched. On a review row this is the
            # pre-selected suggestion, not a decision -- `status` is what says
            # which of the two it is.
            detection.match_id = best.entry.id

        detection.crop.save(
            f'scan{scan.pk}-spine{spine.box[0]}.jpg',
            ContentFile(crop_bytes),
            save=False,
        )
        # Pointers are validated to reference strictly earlier crops, so the
        # target is always already saved and has a pk.
        if read.duplicate_of is not None:
            original = by_index.get(read.duplicate_of)
            if original is not None:
                detection.duplicate_of = original

        detection.save()
        by_index[read.index] = detection
        detections.append(detection)

    return detections


def _normalize_stored_image(scan: Scan, image) -> None:
    """Rewrite the upload as JPEG if it arrived as anything else.

    Only touches the file when the extension is not already JPEG, so a normal
    upload costs nothing. The old file is deleted rather than orphaned.
    """
    name = (scan.image.name or '').lower()
    if name.endswith(('.jpg', '.jpeg')):
        return

    previous = scan.image.name
    scan.image.save(
        f'{Path(previous).stem}.jpg',
        ContentFile(to_jpeg_bytes(image)),
        save=False,
    )
    scan.save(update_fields=['image', 'updated_at'])
    if previous and previous != scan.image.name:
        scan.image.storage.delete(previous)
    logger.info('Scan %s: normalized %s to JPEG', scan.pk, previous)
