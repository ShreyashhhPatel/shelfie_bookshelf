import logging

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CatalogBook, Detection, LibraryEntry, Scan
from .serializers import (
    CatalogBookSearchSerializer,
    DetectionSerializer,
    LibraryEntrySerializer,
    ScanSerializer,
)
from .services import pipeline, vlm_read
from .services.pipeline import apply_read_to_detection, auto_confirm_if_high_confidence, run_pipeline

logger = logging.getLogger(__name__)


class ScanCreateView(APIView):
    def post(self, request):
        image = request.FILES.get("image")
        if not image:
            return Response({"error": "image file is required"}, status=400)

        scan = Scan.objects.create(image=image)
        try:
            run_pipeline(scan)
        except Exception:
            # Request-level failure boundary: whatever goes wrong inside the
            # pipeline, the client gets a scan with status="failed" and a
            # retry affordance -- never a bare 500 / blank screen.
            logger.exception("Pipeline failed for scan %s", scan.id)
            scan.status = Scan.STATUS_FAILED
            scan.save(update_fields=["status"])

        serializer = ScanSerializer(scan, context={"request": request})
        return Response(serializer.data, status=201)


class ScanDetailView(APIView):
    def get(self, request, pk):
        scan = get_object_or_404(Scan, pk=pk)
        serializer = ScanSerializer(scan, context={"request": request})
        return Response(serializer.data)


class DetectionConfirmView(APIView):
    def post(self, request, pk):
        detection = get_object_or_404(Detection, pk=pk)
        book_id = request.data.get("book_id")
        custom_title = (request.data.get("custom_title") or "").strip()
        custom_author = (request.data.get("custom_author") or "").strip()

        book = None
        if book_id:
            book = get_object_or_404(CatalogBook, pk=book_id)
        elif not custom_title:
            return Response({"error": "book_id or custom_title is required"}, status=400)

        entry = LibraryEntry.objects.create(
            book=book,
            custom_title="" if book else custom_title,
            custom_author="" if book else custom_author,
            source_detection=detection,
        )
        detection.status = Detection.STATUS_CONFIRMED
        detection.chosen_book = book
        detection.save(update_fields=["status", "chosen_book"])
        return Response(LibraryEntrySerializer(entry).data, status=201)


class DetectionDiscardView(APIView):
    def post(self, request, pk):
        detection = get_object_or_404(Detection, pk=pk)
        detection.status = Detection.STATUS_DISCARDED
        detection.save(update_fields=["status"])
        return Response(DetectionSerializer(detection, context={"request": request}).data)


class DetectionRetryView(APIView):
    def post(self, request, pk):
        detection = get_object_or_404(Detection, pk=pk)
        if not detection.crop:
            return Response({"error": "no crop stored for this detection"}, status=400)

        crop_bytes = detection.crop.read()
        reads = vlm_read.read_spines_batch([crop_bytes])
        catalog = pipeline._catalog_as_dicts()
        apply_read_to_detection(detection, reads[0], catalog)
        detection.save()
        auto_confirm_if_high_confidence(detection)
        return Response(DetectionSerializer(detection, context={"request": request}).data)


class CatalogSearchView(APIView):
    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response([])
        results = CatalogBook.objects.filter(Q(title__icontains=query) | Q(author__icontains=query))[:20]
        return Response(CatalogBookSearchSerializer(results, many=True).data)


class LibraryListView(APIView):
    def get(self, request):
        entries = LibraryEntry.objects.select_related("book").order_by("-created_at")
        return Response(LibraryEntrySerializer(entries, many=True).data)


class LibraryDetailView(APIView):
    def patch(self, request, pk):
        entry = get_object_or_404(LibraryEntry, pk=pk)
        if "custom_title" in request.data:
            entry.custom_title = request.data["custom_title"]
        if "custom_author" in request.data:
            entry.custom_author = request.data["custom_author"]
        entry.save()
        return Response(LibraryEntrySerializer(entry).data)

    def delete(self, request, pk):
        entry = get_object_or_404(LibraryEntry, pk=pk)
        entry.delete()
        return Response(status=204)
