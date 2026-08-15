"""Image handling between the detector and whatever reads the crops.

Pure Pillow. Nothing here knows about Django, models, or the network, so it
can be exercised on a bare image in a REPL.

The job is unglamorous but it is where read accuracy is won or lost: a spine
cropped a few pixels too tight, or left lying on its side, is a spine the
vision model cannot read.
"""

import io

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

# iPhones shoot HEIC by default and Pillow cannot open it unaided. The mobile
# client converts before upload, but the API is public and anything can POST
# to it, so the server refuses to depend on the client having done the right
# thing. Registering here means every entry point gets it -- the pipeline, a
# management command, a REPL -- because this is the module they all decode
# through. Idempotent, so importing repeatedly is harmless.
register_heif_opener()

from ..constants import (
    CROP_PADDING_PX,
    CROP_TARGET_LONG_EDGE,
    JPEG_QUALITY,
    MAX_IMAGE_LONG_EDGE,
    SPINE_ROTATION_DEGREES,
    TALL_NARROW_ASPECT_RATIO,
)

# Pixel box in the source image, (x1, y1, x2, y2), origin top-left.
Box = tuple[int, int, int, int]


def load_image(data: bytes | io.BytesIO) -> Image.Image:
    """Decode bytes to a normalized RGB image.

    `exif_transpose` is not optional. Phone cameras record orientation in EXIF
    rather than rotating pixels, so a photo taken in portrait decodes sideways
    -- every box the detector returns would then be wrong in the same way, and
    the bug looks like a detector problem rather than a decode problem.
    """
    buffer = io.BytesIO(data) if isinstance(data, bytes) else data
    image = Image.open(buffer)
    image = ImageOps.exif_transpose(image)
    return image.convert('RGB')


def downscale(image: Image.Image, long_edge: int = MAX_IMAGE_LONG_EDGE) -> Image.Image:
    """Shrink so the longest edge is at most `long_edge`. Never upscales."""
    width, height = image.size
    longest = max(width, height)
    if longest <= long_edge:
        return image
    scale = long_edge / longest
    return image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)


def pad_box(box: Box, image_size: tuple[int, int], padding: int = CROP_PADDING_PX) -> Box:
    """Grow a box by `padding` on all sides, clamped to the image.

    Detector boxes clip tight and routinely shave the first or last character
    off a title. Clamping matters as much as the padding: a spine at the edge
    of the frame would otherwise produce a box with negative coordinates, which
    Pillow silently interprets as a crop from outside the image.
    """
    width, height = image_size
    x1, y1, x2, y2 = box
    return (
        max(0, x1 - padding),
        max(0, y1 - padding),
        min(width, x2 + padding),
        min(height, y2 + padding),
    )


def is_tall_narrow(size: tuple[int, int], ratio: float = TALL_NARROW_ASPECT_RATIO) -> bool:
    """True when a crop is upright enough to be a vertical spine."""
    width, height = size
    if width <= 0:
        return False
    return (height / width) >= ratio


def crop_spine(
    image: Image.Image,
    box: Box,
    padding: int = CROP_PADDING_PX,
    rotate: bool = True,
) -> Image.Image:
    """Cut one spine out of a shelf photo, upright and ready to read.

    A shelved book is vertical, so its title reads bottom-to-top and the crop
    comes out as a tall ribbon of sideways text. Vision models read that far
    worse than horizontal text, so tall-narrow crops are rotated.

    Books stacked flat are already horizontal and are left alone -- which is
    what the aspect check is for, rather than rotating unconditionally.
    """
    padded = pad_box(box, image.size, padding)
    crop = image.crop(padded)

    if rotate and is_tall_narrow(crop.size):
        crop = crop.rotate(SPINE_ROTATION_DEGREES, expand=True)

    return crop


def upscale_for_reading(
    image: Image.Image, long_edge: int = CROP_TARGET_LONG_EDGE
) -> Image.Image:
    """Enlarge a small crop so its lettering survives JPEG encoding.

    A spine occupying 90px of a shelf photo is legible to a human squinting at
    the original and illegible after compression. Upscaling adds no detail, but
    it stops the encoder from destroying the little that is there.
    """
    width, height = image.size
    longest = max(width, height)
    if longest == 0 or longest >= long_edge:
        return image
    scale = long_edge / longest
    return image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)


def to_jpeg_bytes(image: Image.Image, quality: int = JPEG_QUALITY) -> bytes:
    """Encode to JPEG bytes.

    RGB conversion is forced because a crop taken from a PNG or a palettized
    source carries a mode JPEG cannot represent, and Pillow raises rather than
    converting for you.
    """
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='JPEG', quality=quality, optimize=True)
    return buffer.getvalue()


def prepare_crop(image: Image.Image, box: Box) -> bytes:
    """Box in, readable JPEG out. The whole crop path in one call."""
    return to_jpeg_bytes(upscale_for_reading(crop_spine(image, box)))
