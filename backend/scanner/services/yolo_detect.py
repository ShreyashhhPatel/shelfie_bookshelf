"""Spine detection with a pretrained YOLOv8 checkpoint.

This module locates books. It never reads them -- no text leaves here, only
boxes. Reading is the vision-language model's job in a later phase, and
keeping the two apart is what lets detection stay local and free while only
the read costs money.

The checkpoint is COCO-pretrained and not fine-tuned on shelves. Class 73 is
"book", which COCO labels generously enough to cover spines, and everything
else the model finds in a photo of a room is discarded.
"""

import logging
import threading
from dataclasses import dataclass

from PIL import Image

from ..constants import (
    BOOK_CLASS_ID,
    DETECTION_CONFIDENCE_THRESHOLD,
    DETECTION_IOU_THRESHOLD,
    MAX_DETECTIONS_PER_IMAGE,
    YOLO_MODEL_NAME,
    YOLO_MODEL_PATH,
)
from .image_utils import Box

logger = logging.getLogger(__name__)

# Loading the checkpoint takes a second or two and allocates a few hundred MB.
# Doing it per request would dominate scan latency, so the model is a
# process-level singleton behind a lock -- Django's threaded dev server can
# otherwise start two loads concurrently on the first two requests.
_model = None
_model_lock = threading.Lock()


@dataclass(frozen=True)
class SpineDetection:
    """One detected book spine.

    Deliberately not a Django model. The detector returns plain values so it
    can be run against a bare image with no database, and so the caller decides
    what is worth persisting.
    """

    box: Box
    confidence: float

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]

    @property
    def area(self) -> int:
        return self.width * self.height


def get_model():
    """Return the shared YOLO instance, loading it on first use.

    Imported lazily inside the function: `ultralytics` pulls in torch, which
    costs seconds of import time and hundreds of MB. Django's autoreloader
    imports every module in the app on every code change, so a module-level
    import here would make the whole development loop slow.
    """
    global _model

    if _model is not None:
        return _model

    with _model_lock:
        # Re-checked inside the lock: another thread may have loaded it while
        # this one was waiting.
        if _model is None:
            from ultralytics import YOLO

            YOLO_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            # A bare filename lets ultralytics fetch the weights on first run;
            # an existing local path is used as-is and never hits the network.
            source = (
                str(YOLO_MODEL_PATH) if YOLO_MODEL_PATH.exists() else YOLO_MODEL_NAME
            )
            logger.info('Loading YOLO checkpoint from %s', source)
            _model = YOLO(source)

    return _model


def detect_book_boxes(
    image: Image.Image,
    confidence: float = DETECTION_CONFIDENCE_THRESHOLD,
) -> list[SpineDetection]:
    """Find book spines in a shelf photo.

    Returns boxes sorted left to right, which is the order a reader would take
    them off the shelf and therefore the order the review screen should show
    them in. Returns an empty list rather than raising when nothing is found --
    a photo of a wall is a valid input with no books in it.
    """
    model = get_model()

    results = model.predict(
        image,
        conf=confidence,
        iou=DETECTION_IOU_THRESHOLD,
        max_det=MAX_DETECTIONS_PER_IMAGE,
        # Filtering in the model is cheaper than filtering after, and makes it
        # impossible to forget: a shelf photo also contains vases and clocks.
        classes=[BOOK_CLASS_ID],
        verbose=False,
    )

    detections: list[SpineDetection] = []
    for result in results:
        boxes = getattr(result, 'boxes', None)
        if boxes is None:
            continue
        for xyxy, conf in zip(boxes.xyxy.tolist(), boxes.conf.tolist()):
            x1, y1, x2, y2 = (int(round(value)) for value in xyxy)
            # Degenerate boxes occur at image edges and would crop to nothing.
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(SpineDetection(box=(x1, y1, x2, y2), confidence=float(conf)))

    detections.sort(key=lambda detection: detection.box[0])
    logger.info('Detected %d book spine(s)', len(detections))
    return detections
