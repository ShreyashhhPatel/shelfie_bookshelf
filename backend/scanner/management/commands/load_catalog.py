"""Load catalog/catalog.csv into CatalogBook.

Idempotent: rerunning updates rows in place rather than duplicating them, so
this is safe to run on every clean clone and after every edit to the CSV.
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from scanner.constants import normalize_author, normalize_title, split_multi
from scanner.models import CatalogBook

DEFAULT_PATH = Path(settings.BASE_DIR) / 'catalog' / 'catalog.csv'

REQUIRED_COLUMNS = {
    'title',
    'author',
    'year',
    'alt_titles',
    'is_omnibus',
    'contained_titles',
}


class Command(BaseCommand):
    help = 'Load the canonical book catalog from CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path',
            type=Path,
            default=DEFAULT_PATH,
            help=f'CSV to load (default: {DEFAULT_PATH}).',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing catalog rows first. Detections pointing at a '
                 'deleted row have their match nulled, not removed.',
        )

    def handle(self, *args, **options):
        path: Path = options['path']
        if not path.exists():
            raise CommandError(f'Catalog not found: {path}')

        with path.open(newline='', encoding='utf-8') as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f'Missing columns: {", ".join(sorted(missing))}')
            rows = list(reader)

        created = updated = 0
        seen: dict[tuple[str, str], int] = {}

        with transaction.atomic():
            if options['clear']:
                deleted, _ = CatalogBook.objects.all().delete()
                self.stdout.write(f'Cleared {deleted} existing row(s).')

            for line, row in enumerate(rows, start=2):
                title = (row['title'] or '').strip()
                author = (row['author'] or '').strip()
                if not title or not author:
                    raise CommandError(f'Line {line}: title and author are required.')

                # The (norm_title, norm_author) pair is the real key -- title
                # alone is not unique, by design. See AMBIGUITIES.md case 4.
                key = (normalize_title(title), normalize_author(author))
                if key in seen:
                    raise CommandError(
                        f'Line {line}: "{title}" by {author} duplicates line {seen[key]}.'
                    )
                seen[key] = line

                year = (row['year'] or '').strip()
                book, was_created = CatalogBook.objects.update_or_create(
                    norm_title=key[0],
                    norm_author=key[1],
                    defaults={
                        'title': title,
                        'author': author,
                        'year': int(year) if year else None,
                        'alt_titles': split_multi(row['alt_titles']),
                        'is_omnibus': (row['is_omnibus'] or '').strip().lower() == 'true',
                        'contained_titles': split_multi(row['contained_titles']),
                    },
                )
                created += was_created
                updated += not was_created

            self._warn_on_dangling_contents()

        total = CatalogBook.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f'Catalog loaded: {created} created, {updated} updated, {total} total.'
            )
        )

    def _warn_on_dangling_contents(self):
        """Flag omnibus contents that have no standalone row.

        Not an error -- an omnibus is allowed to be the only copy of a work in
        the catalog. But it is almost always a typo in the CSV, and it is
        invisible until the matcher mysteriously never returns that volume.
        """
        known = set(CatalogBook.objects.values_list('norm_title', flat=True))
        for book in CatalogBook.objects.filter(is_omnibus=True):
            dangling = [
                contained
                for contained in book.contained_titles
                if normalize_title(contained) not in known
            ]
            if dangling:
                self.stdout.write(
                    self.style.WARNING(
                        f'{book.title}: no standalone row for {", ".join(dangling)}'
                    )
                )
