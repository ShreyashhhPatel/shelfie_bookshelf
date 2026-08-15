"""Scanner API routes.

Only the pipeline-free endpoints exist in this phase. Scan upload and the
review actions get added here as the phases that implement them land.
"""

from django.urls import path

from . import views

app_name = 'scanner'

urlpatterns = [
    path('catalog/search/', views.CatalogSearchView.as_view(), name='catalog-search'),
    path('library/', views.LibraryListCreateView.as_view(), name='library-list'),
    path('library/<int:pk>/', views.LibraryDetailView.as_view(), name='library-detail'),
]
