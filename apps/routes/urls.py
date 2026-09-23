from django.urls import path

from . import views

urlpatterns = [
    path("ruta/", views.ruta, name="ruta"),
    path("ruta/crear", views.ruta_crear, name="ruta_crear"),
    path("ruta/<int:pk>/modo", views.modo_ruta, name="modo_ruta"),
    path("ruta/<int:pk>/progreso", views.progreso, name="ruta_progreso"),
    path("ruta/parada/<int:pk>/estado", views.parada_estado, name="parada_estado"),
]
