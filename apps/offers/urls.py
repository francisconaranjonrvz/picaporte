from django.urls import path

from . import views

urlpatterns = [
    path("ofertas/", views.ofertas, name="ofertas"),
    path("ofertas/importar", views.importar, name="ofertas_importar"),
    path("ofertas/recruzar", views.recruzar, name="ofertas_recruzar"),
]
