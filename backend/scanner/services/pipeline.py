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
from contextlib import contextmanager

from django.core.files.base import ContentFile
from django.db import transaction

from ..models import Detection, Scan
from .image_utils import load_image, prepare_crop
from .matcher import catalog_entries, match
from .vlm_read import VlmReadError, read_spines
from .yolo_detect import detect_book_boxes

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

        _set_status(scan, Scan.Status.DETECTING)
        with timer.stage('detect'):
            spines = detect_book_boxes(image)

        if not spines:
            scan.timings = timer.timings
            _set_status(scan, Scan.Status.COMPLETE, timings=True)
            logger.info('Scan %s found no spines', scan.pk)
            return scan

        with timer.stage('crop'):
            crops = [prepare_crop(image, spine.box) for spine in spines]

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
        scan.error = str(cause)
        _set_status(scan, Scan.Status.FAILED, timings=True, error=True)
        logger.warning('Scan %s failed during read: %s', scan.pk, cause)

    except Exception as cause:  # noqa: BLE001 - the record must carry the failure
        scan.timings = timer.timings
        scan.error = f'{type(cause).__name__}: {cause}'
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
        fields.append('error')
    scan.save(update_fields=fields)


@transaction.atomic
def _persist(scan, spines, crops, reads, results) -> list[Detection]:
    """Write one Detection per spine.

    Atomic so a photo is all-or-nothing: a half-written scan would show the
    user a review screen missing books they can see in the photo.
    """
    detections = []

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
        detection.save()
        detections.append(detection)

    return detections
