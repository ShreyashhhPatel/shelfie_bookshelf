"""The two endpoints that need no pipeline behind them.

Catalog search and the library are the ends of the flow: one is the reference
data a match resolves *to*, the other is what a confirmed match becomes. Both
are useful and testable before a single spine has been detected, which is why
they land in this phase.
"""

from django.db.models import Case, IntegerField, Q, Value, When
from rest_framework import generics

from .constants import normalize_author, normalize_title
from .models import CatalogBook, LibraryBook
from .serializers import CatalogBookSerializer, LibraryBookSerializer


class CatalogSearchView(generics.ListAPIView):
    """GET /api/catalog/search/?q=

    Deliberately broad. This backs a human typing into a search box at the
    review step, so recall matters more than precision -- "dune" should return
    the whole Herbert cluster and let the reader pick.

    This is *not* the matcher. Spine matching has the opposite priorities and
    must not reuse this ranking. See AMBIGUITIES.md, case 1.
    """

    serializer_class = CatalogBookSerializer

    def get_queryset(self):
        query = self.request.query_params.get('q', '').strip()
        if not query:
            return CatalogBook.objects.none()

        title_query = normalize_title(query)
        author_query = normalize_author(query)

        matches = Q(norm_title__icontains=title_query) | Q(
            norm_author__icontains=author_query
        )

        # Alternate titles live in a JSONField, so they are filtered in Python
        # rather than in SQL. Fine at catalog scale (109 rows, read-mostly,
        # loaded once); the fix when it stops being fine is a denormalized
        # alt-title table, not a cleverer query.
        alt_matches = [
            book.pk
            for book in CatalogBook.objects.exclude(alt_titles=[]).only(
                'pk', 'alt_titles'
            )
            if any(title_query in normalize_title(alt) for alt in book.alt_titles)
        ]
        if alt_matches:
            matches |= Q(pk__in=alt_matches)

        # Exact title first, then prefix, then anything else. Without this the
        # shortest and most likely answer sorts alphabetically into the middle
        # of its own sequels.
        rank = Case(
            When(norm_title=title_query, then=Value(0)),
            When(norm_title__startswith=title_query, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )

        return (
            CatalogBook.objects.filter(matches)
            .annotate(rank=rank)
            .order_by('rank', 'title')
        )


class LibraryListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/library/

    POST takes either a catalog_book_id or a bare title, so the review step can
    keep a book the catalog has never heard of.
    """

    serializer_class = LibraryBookSerializer

    def get_queryset(self):
        queryset = LibraryBook.objects.select_related('catalog_book')
        query = self.request.query_params.get('q', '').strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(author__icontains=query)
            )
        return queryset


class LibraryDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET/PATCH/DELETE /api/library/<pk>/

    PATCH is here so a reader can fix a title after the fact without deleting
    and re-adding, which would lose the link back to the detection that found it.
    """

    serializer_class = LibraryBookSerializer
    queryset = LibraryBook.objects.select_related('catalog_book')
