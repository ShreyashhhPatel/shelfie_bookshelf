"""The two endpoints that need no pipeline behind them.

Catalog search and the library are the ends of the flow: one is the reference
data a match resolves *to*, the other is what a confirmed match becomes. Both
are useful and testable before a single spine has been detected, which is why
they land in this phase.
"""

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .constants import normalize_author, normalize_title
from .models import CatalogBook, Detection, LibraryBook, Scan
from .serializers import (
    CatalogBookSerializer,
    DetectionSerializer,
    LibraryBookSerializer,
    ScanCreateSerializer,
    ScanSerializer,
)
from .services.pipeline import run_scan


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


class ScanCreateView(generics.CreateAPIView):
    """POST /api/scans/

    Runs the whole pipeline inside the request and returns the finished scan,
    so the client gets results from one call with no polling.

    This is the wrong shape for production and knowingly so. The read stage is
    seconds of network wait, and holding a request open for it ties up a worker
    and a SQLite write lock. Moving the pipeline onto a queue is the first
    scaling change, and it only changes this view.
    """

    queryset = Scan.objects.all()
    serializer_class = ScanCreateSerializer
    parser_classes = (MultiPartParser, FormParser)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scan = serializer.save()

        run_scan(scan)

        scan.refresh_from_db()
        detail = ScanSerializer(scan, context=self.get_serializer_context())
        # 201 even for a failed pipeline: the Scan resource was created, and
        # its own status field carries the failure. The client reads one place
        # for outcome rather than branching on the HTTP code.
        return Response(detail.data, status=status.HTTP_201_CREATED)


class ScanDetailView(generics.RetrieveAPIView):
    """GET /api/scans/<pk>/"""

    serializer_class = ScanSerializer
    queryset = Scan.objects.prefetch_related('detections__match')


class DetectionConfirmView(APIView):
    """POST /api/detections/<pk>/confirm/

    The review step's yes. Accepts an optional catalog_book_id so a correction
    ("it's the Batuman one, not the Dostoevsky") confirms in the same call.
    """

    def post(self, request, pk: int):
        detection = get_object_or_404(Detection, pk=pk)

        catalog_book = detection.match
        override_id = request.data.get('catalog_book_id')
        if override_id is not None:
            catalog_book = get_object_or_404(CatalogBook, pk=override_id)

        title = (request.data.get('title') or '').strip()
        author = (request.data.get('author') or '').strip()

        if catalog_book is not None:
            title = title or catalog_book.title
            author = author or catalog_book.author
        else:
            # Keeping a book the catalog has never heard of. The raw read is
            # the only record of it, so it has to be usable as one.
            title = title or detection.raw_title.strip()
            author = author or detection.raw_author.strip()
            if not title:
                return Response(
                    {'title': ['Required when the detection has no catalog match.']},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        existing = (
            LibraryBook.objects.filter(catalog_book=catalog_book).first()
            if catalog_book is not None
            else None
        )
        if existing is not None:
            # Idempotent rather than an error: scanning a shelf twice is normal
            # and should not make the review screen unusable.
            detection.match = catalog_book
            detection.status = Detection.Status.CONFIRMED
            detection.save(update_fields=['match', 'status'])
            return Response(
                LibraryBookSerializer(existing).data, status=status.HTTP_200_OK
            )

        with transaction.atomic():
            library_book = LibraryBook.objects.create(
                catalog_book=catalog_book,
                title=title,
                author=author,
                source=LibraryBook.Source.SCAN,
                detection=detection,
            )
            detection.match = catalog_book
            detection.status = Detection.Status.CONFIRMED
            detection.save(update_fields=['match', 'status'])

        return Response(
            LibraryBookSerializer(library_book).data, status=status.HTTP_201_CREATED
        )


class DetectionDiscardView(APIView):
    """POST /api/detections/<pk>/discard/

    The review step's no. The Detection is kept, not deleted -- it is the
    record of what the pipeline saw, and throwing it away would make a false
    positive impossible to study later.
    """

    def post(self, request, pk: int):
        detection = get_object_or_404(Detection, pk=pk)
        detection.status = Detection.Status.DISCARDED
        detection.save(update_fields=['status'])
        return Response(
            DetectionSerializer(detection, context={'request': request}).data
        )
