"""Serializers for the endpoints that need no pipeline behind them."""

from rest_framework import serializers

from .models import CatalogBook, LibraryBook


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
