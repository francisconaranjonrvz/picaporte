from django.urls import path

from . import views

urlpatterns = [
    path("", views.explorar, name="explorar"),
    path("datos/", views.datos, name="datos"),
    path("empresa/<int:pk>/", views.ficha, name="ficha"),
    path("mapa/", views.mapa, name="mapa"),
    path("mapa/datos", views.mapa_datos, name="mapa_datos"),
]
