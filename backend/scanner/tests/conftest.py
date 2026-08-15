"""Shared fixtures.

Everything here is offline. The catalog comes from the real CSV rather than a
hand-built copy, so a test failing means the code disagrees with the data the
app actually ships.
"""

import csv
import io

import pytest
from PIL import Image

from scanner.constants import BACKEND_DIR, split_multi
from scanner.services.matcher import CatalogEntry


@pytest.fixture(scope='session')
def catalog() -> list[CatalogEntry]:
    """All 109 rows, including the six planted ambiguities."""
    path = BACKEND_DIR / 'catalog' / 'catalog.csv'
    with path.open(newline='', encoding='utf-8') as handle:
        return [
            CatalogEntry(
                id=index,
                title=row['title'],
                author=row['author'],
                alt_titles=tuple(split_multi(row['alt_titles'])),
                is_omnibus=row['is_omnibus'].strip().lower() == 'true',
            )
            for index, row in enumerate(csv.DictReader(handle), start=1)
        ]


@pytest.fixture
def tall_spine_image() -> Image.Image:
    """A crop shaped like a shelved book: tall and narrow."""
    return Image.new('RGB', (60, 400), (120, 40, 40))


@pytest.fixture
def wide_image() -> Image.Image:
    """A crop shaped like a book lying flat. Must not be rotated."""
    return Image.new('RGB', (400, 120), (40, 80, 120))


@pytest.fixture
def heic_bytes() -> bytes:
    """A real HEIC file, encoded in-memory by pillow-heif.

    Written through Pillow's normal save path, so this exercises the same
    decoder registration the pipeline depends on rather than a stub.
    """
    from pillow_heif import register_heif_opener

    register_heif_opener()
    source = Image.new('RGB', (240, 160), (200, 90, 60))
    buffer = io.BytesIO()
    source.save(buffer, format='HEIF')
    return buffer.getvalue()
