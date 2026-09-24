from django.urls import path

from . import views

urlpatterns = [
    path("ruta/", views.ruta, name="ruta"),
    path("ruta/crear", views.ruta_crear, name="ruta_crear"),
    path("ruta/<int:pk>/modo", views.modo_ruta, name="modo_ruta"),
    path("ruta/<int:pk>/progreso", views.progreso, name="ruta_progreso"),
    path("ruta/parada/<int:pk>/estado", views.parada_estado, name="parada_estado"),
    path("ruta/parada/<int:pk>/quitar", views.quitar, name="parada_quitar"),
    path("ruta/<int:pk>/ordenar", views.ordenar, name="ruta_ordenar"),
    path("ruta/anadir/<int:company_pk>", views.anadir, name="ruta_anadir"),
]
