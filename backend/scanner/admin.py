from django.contrib import admin

from .models import CatalogBook, Detection, LibraryEntry, Scan

admin.site.register(CatalogBook)
admin.site.register(Scan)
admin.site.register(Detection)
admin.site.register(LibraryEntry)
