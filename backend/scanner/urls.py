from django.urls import path

from . import views

urlpatterns = [
    path("scans/", views.ScanCreateView.as_view(), name="scan-create"),
    path("scans/<int:pk>/", views.ScanDetailView.as_view(), name="scan-detail"),
    path("detections/<int:pk>/confirm/", views.DetectionConfirmView.as_view(), name="detection-confirm"),
    path("detections/<int:pk>/discard/", views.DetectionDiscardView.as_view(), name="detection-discard"),
    path("detections/<int:pk>/retry/", views.DetectionRetryView.as_view(), name="detection-retry"),
    path("catalog/search/", views.CatalogSearchView.as_view(), name="catalog-search"),
    path("library/", views.LibraryListView.as_view(), name="library-list"),
    path("library/<int:pk>/", views.LibraryDetailView.as_view(), name="library-detail"),
]
