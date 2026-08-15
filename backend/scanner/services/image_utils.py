"""
Image handling: force-convert uploads to JPEG (HEIC-safe) and rotate
tall-narrow crops so vertical spine text reads upright before the VLM sees
it. Deliberately does not downscale -- format conversion is lossless enough,
while downscaling is what blurs small spine text and hurts read accuracy.
"""

import io

import pillow_heif
from PIL import Image

from scanner.constants import TALL_NARROW_ASPECT_RATIO

pillow_heif.register_heif_opener()

JPEG_QUALITY = 95


def to_jpeg_bytes(file_obj) -> bytes:
    """Decodes any Pillow/HEIF-supported image and re-encodes as high-quality JPEG."""
    image = Image.open(file_obj).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def load_rgb_image(jpeg_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")


def image_to_jpeg_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def crop_with_padding(image: Image.Image, bbox: tuple[int, int, int, int], padding: int) -> Image.Image:
    x1, y1, x2, y2 = bbox
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(image.width, x2 + padding)
    y2 = min(image.height, y2 + padding)
    return image.crop((x1, y1, x2, y2))


def rotate_if_tall_narrow(crop: Image.Image) -> Image.Image:
    """Spine text runs vertically -- rotate crops taller than they are wide before OCR."""
    width, height = crop.size
    if width == 0 or height / width < TALL_NARROW_ASPECT_RATIO:
        return crop
    return crop.rotate(-90, expand=True)
