from django.urls import path

from . import views

urlpatterns = [
    path("favoritas/", views.favoritas, name="favoritas"),
    path("favoritas/orden", views.favorites_reorder, name="favorites_reorder"),
    path("empresa/<int:pk>/favorita", views.favorite_toggle, name="favorite_toggle"),
    path("empresa/<int:pk>/favorita/editar", views.favorite_update, name="favorite_update"),
    path("empresa/<int:pk>/estado", views.status_update, name="status_update"),
    path("empresa/<int:pk>/seguimiento", views.visit_update, name="visit_update"),
    path("empresa/<int:pk>/notas", views.notes, name="notes"),
    path("empresa/<int:pk>/notas/nueva", views.note_add, name="note_add"),
]
