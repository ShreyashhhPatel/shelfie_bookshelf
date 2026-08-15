"""API shapes. Mirrored by hand in mobile/src/api/types.ts."""

from rest_framework import serializers

from .models import CatalogBook, Detection, LibraryBook, Scan
from .services.vlm_read import ReadErrorCode


class CatalogBookSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogBook
        fields = (
            'id',
            'title',
            'author',
            'year',
            'alt_titles',
            'is_omnibus',
            'contained_titles',
        )
        read_only_fields = fields


class LibraryBookSerializer(serializers.ModelSerializer):
    """Read nests the catalog row; write takes its id.

    Title and author are optional on write *only* when a catalog book is
    given -- they are copied from it below. A book the catalog does not have
    must supply its own title, which is what makes "keep it anyway" work at
    the review step.
    """

    catalog_book = CatalogBookSerializer(read_only=True)
    catalog_book_id = serializers.PrimaryKeyRelatedField(
        queryset=CatalogBook.objects.all(),
        source='catalog_book',
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = LibraryBook
        fields = (
            'id',
            'title',
            'author',
            'source',
            'catalog_book',
            'catalog_book_id',
            'added_at',
        )
        read_only_fields = ('id', 'added_at')
        extra_kwargs = {
            'title': {'required': False},
            'author': {'required': False},
        }

    def validate(self, attrs):
        catalog_book = attrs.get('catalog_book')
        if catalog_book is None and not (attrs.get('title') or '').strip():
            raise serializers.ValidationError(
                {'title': 'Required when no catalog_book_id is given.'}
            )
        if catalog_book is not None:
            # The catalog row is the authority on its own name. Snapshotting
            # it here keeps the library readable if the row is later removed.
            attrs.setdefault('title', catalog_book.title)
            attrs.setdefault('author', catalog_book.author)
        return attrs

    def validate_catalog_book_id(self, value):
        if value is not None and LibraryBook.objects.filter(catalog_book=value).exists():
            raise serializers.ValidationError('Already in the library.')
        return value


class DetectionSerializer(serializers.ModelSerializer):
    """One spine and everything known about it.

    `candidates` is passed through as stored rather than re-serialized: it is
    already the ranked list the review screen needs, computed once at scan
    time, and recomputing it per request would mean reloading the catalog.
    """

    match = CatalogBookSerializer(read_only=True)
    crop_url = serializers.SerializerMethodField()

    class Meta:
        model = Detection
        fields = (
            'id',
            'bbox',
            'crop_url',
            'confidence',
            'raw_title',
            'raw_author',
            'candidates',
            'match',
            'margin',
            'status',
            'duplicate_of',
        )
        read_only_fields = fields

    def get_crop_url(self, detection) -> str | None:
        if not detection.crop:
            return None
        request = self.context.get('request')
        url = detection.crop.url
        # Absolute, because the client is a phone on the LAN and a relative
        # media path resolves against the device, not the API host.
        return request.build_absolute_uri(url) if request else url


class ScanSerializer(serializers.ModelSerializer):
    detections = DetectionSerializer(many=True, read_only=True)
    image_url = serializers.SerializerMethodField()
    counts = serializers.SerializerMethodField()
    is_retryable = serializers.SerializerMethodField()

    class Meta:
        model = Scan
        fields = (
            'id',
            'status',
            'error',
            'error_code',
            'is_retryable',
            'image_url',
            'timings',
            'counts',
            'detections',
            'created_at',
        )
        read_only_fields = fields

    def get_image_url(self, scan) -> str | None:
        if not scan.image:
            return None
        request = self.context.get('request')
        return request.build_absolute_uri(scan.image.url) if request else scan.image.url

    def get_is_retryable(self, scan) -> bool:
        """Whether trying the same photo again could plausibly work.

        Computed here rather than left to the client, so the rule lives with
        the codes it describes instead of being duplicated per platform.
        """
        if not scan.error_code:
            return False
        try:
            return ReadErrorCode(scan.error_code).is_retryable
        except ValueError:
            return False

    def get_counts(self, scan) -> dict:
        """Pre-counted so the client does not have to filter the list itself.

        `total` excludes duplicates: a shelf of 20 books that the detector
        boxed 24 times has 20 books on it, and telling the user otherwise is
        just wrong.
        """
        detections = list(scan.detections.all())
        unique = [d for d in detections if d.duplicate_of_id is None]
        return {
            'total': len(unique),
            'auto_matched': sum(
                1 for d in unique if d.status == Detection.Status.AUTO_MATCHED
            ),
            'needs_review': sum(
                1 for d in unique if d.status == Detection.Status.NEEDS_REVIEW
            ),
            'duplicates': len(detections) - len(unique),
        }


class ScanCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scan
        fields = ('id', 'image')
