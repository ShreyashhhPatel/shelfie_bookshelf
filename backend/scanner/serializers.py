from rest_framework import serializers

from .models import CatalogBook, Detection, LibraryEntry, Scan


class DetectionSerializer(serializers.ModelSerializer):
    crop_url = serializers.SerializerMethodField()

    class Meta:
        model = Detection
        fields = [
            "id",
            "bbox",
            "crop_url",
            "read_title",
            "read_author",
            "read_spine_color",
            "status",
            "confidence",
            "margin",
            "candidates",
            "chosen_book",
        ]

    def get_crop_url(self, obj):
        if not obj.crop:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.crop.url) if request else obj.crop.url


class ScanSerializer(serializers.ModelSerializer):
    detections = DetectionSerializer(many=True, read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Scan
        fields = [
            "id",
            "image_url",
            "status",
            "timings",
            "used_full_image_fallback",
            "empty_result",
            "created_at",
            "detections",
        ]

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class CatalogBookSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogBook
        fields = ["id", "title", "author", "year", "series", "spine_color", "spine_hex"]


class LibraryEntrySerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="display_title", read_only=True)
    author = serializers.CharField(source="display_author", read_only=True)

    class Meta:
        model = LibraryEntry
        fields = ["id", "book", "title", "author", "created_at"]
