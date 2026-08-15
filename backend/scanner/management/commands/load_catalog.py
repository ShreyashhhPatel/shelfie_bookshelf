import csv

from django.conf import settings
from django.core.management.base import BaseCommand

from scanner.models import CatalogBook


def _split_pipe_list(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()] if value else []


def _normalize_hex(value: str) -> str:
    """
    Accepts "#RRGGBB", "RRGGBB" or blank. Anything else is dropped rather than
    stored: a half-parsed colour would render as a wrong swatch on a review
    card, which is worse than showing no swatch at all.
    """
    text = (value or "").strip().upper().lstrip("#")
    if len(text) != 6 or any(c not in "0123456789ABCDEF" for c in text):
        return ""
    return f"#{text}"


class Command(BaseCommand):
    help = "Loads catalog/catalog.csv into CatalogBook, replacing any existing rows."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=None, help="Override the CSV path.")

    def handle(self, *args, **options):
        path = options["path"] or settings.CATALOG_CSV_PATH
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        CatalogBook.objects.all().delete()
        books = [
            CatalogBook(
                title=row["title"].strip(),
                author=row["author"].strip(),
                alt_titles=_split_pipe_list(row.get("alt_titles", "")),
                year=int(row["year"]) if row.get("year", "").strip() else None,
                series=row.get("series", "").strip(),
                is_omnibus=row.get("is_omnibus", "").strip().lower() == "true",
                contained_titles=_split_pipe_list(row.get("contained_titles", "")),
                # Optional columns: a CSV written before these existed still loads.
                spine_color=row.get("spine_color", "").strip().lower(),
                spine_hex=_normalize_hex(row.get("spine_hex", "")),
            )
            for row in rows
        ]
        CatalogBook.objects.bulk_create(books)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(books)} catalog entries from {path}"))
