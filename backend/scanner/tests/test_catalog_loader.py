"""
Catalog loading, including the spine-appearance columns.

The colour columns are optional by design: a CSV written before they existed
must still load, and a malformed colour must degrade to "unknown" rather than
to a wrong swatch on a review card.
"""

import pytest
from django.core.management import call_command

from scanner.management.commands.load_catalog import _normalize_hex
from scanner.models import CatalogBook

pytestmark = pytest.mark.django_db

HEADER = "title,author,alt_titles,year,series,is_omnibus,contained_titles,spine_color,spine_hex\n"


def _write_csv(tmp_path, body, header=HEADER, name="catalog.csv"):
    path = tmp_path / name
    path.write_text(header + body, encoding="utf-8")
    return str(path)


# ---------- hex parsing ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#FCE153", "#FCE153"),
        ("fce153", "#FCE153"),  # accepts a bare, lowercase hex
        ("  #c59ec9  ", "#C59EC9"),
        ("", ""),
        ("purple", ""),  # a colour name in the hex column is not a hex
        ("#FFF", ""),  # shorthand is ambiguous here, so it's rejected
        ("#GGGGGG", ""),
        ("#FCE1533", ""),
    ],
)
def test_normalize_hex(raw, expected):
    assert _normalize_hex(raw) == expected


# ---------- loading ----------


def test_loads_spine_appearance(tmp_path):
    path = _write_csv(tmp_path, '"This Is Going to Hurt","Adam Kay","","2017","","false","","yellow","#FCE153"\n')
    call_command("load_catalog", path=path)

    book = CatalogBook.objects.get(title="This Is Going to Hurt")
    assert book.spine_color == "yellow"
    assert book.spine_hex == "#FCE153"


def test_colour_names_are_normalized_to_lowercase(tmp_path):
    path = _write_csv(tmp_path, '"A","B","","","","false","","Purple","#C59EC9"\n')
    call_command("load_catalog", path=path)
    assert CatalogBook.objects.get(title="A").spine_color == "purple"


def test_csv_without_the_colour_columns_still_loads(tmp_path):
    """A catalog authored before spine colour existed must not break the loader."""
    old_header = "title,author,alt_titles,year,series,is_omnibus,contained_titles\n"
    path = _write_csv(tmp_path, '"Dune","Frank Herbert","","1965","Dune Chronicles","false",""\n', header=old_header)
    call_command("load_catalog", path=path)

    book = CatalogBook.objects.get(title="Dune")
    assert book.spine_color == ""
    assert book.spine_hex == ""


def test_unknown_colour_stays_blank_rather_than_defaulting(tmp_path):
    """Blank must mean unknown. Defaulting to white would paint a wrong swatch."""
    path = _write_csv(tmp_path, '"A","B","","","","false","","","not-a-hex"\n')
    call_command("load_catalog", path=path)

    book = CatalogBook.objects.get(title="A")
    assert book.spine_color == ""
    assert book.spine_hex == ""


def test_load_replaces_rather_than_appends(tmp_path):
    first = _write_csv(tmp_path, '"Old","Author","","","","false","","",""\n', name="first.csv")
    call_command("load_catalog", path=first)
    second = _write_csv(tmp_path, '"New","Author","","","","false","","",""\n', name="second.csv")
    call_command("load_catalog", path=second)

    assert list(CatalogBook.objects.values_list("title", flat=True)) == ["New"]


# ---------- the shipped catalog ----------


def test_shipped_catalog_covers_the_test_photo_books():
    """
    The five books in backend/test_photos. A scan of those shelves should
    resolve against the catalog rather than fall through to unmatched.
    """
    call_command("load_catalog")

    expected = {
        "The Art of War": ("Sun Tzu", "red"),
        "The 48 Laws of Power": ("Robert Greene", "red"),
        "This Is Going to Hurt": ("Adam Kay", "yellow"),
        "And Then There Were None": ("Agatha Christie", "black"),
        "The Thirteen Problems": ("Agatha Christie", "purple"),
    }
    for title, (author, color) in expected.items():
        book = CatalogBook.objects.get(title=title)
        assert book.author == author
        assert book.spine_color == color
        assert book.spine_hex.startswith("#")


def test_shipped_catalog_hexes_are_well_formed():
    call_command("load_catalog")
    for book in CatalogBook.objects.exclude(spine_hex=""):
        assert _normalize_hex(book.spine_hex) == book.spine_hex
