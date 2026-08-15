"""URL configuration for the shelfie project."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('scanner.urls')),
]

# Serve uploaded shelf photos and spine crops in development. In production
# this is the web server's job, not Django's.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
