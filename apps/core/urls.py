from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("mapa/", views.mapa, name="mapa"),
    path("favoritas/", views.favoritas, name="favoritas"),
    path("ruta/", views.ruta, name="ruta"),
    path("styleguide", views.styleguide, name="styleguide"),
    path("styleguide/demo", views.styleguide_demo, name="styleguide_demo"),
    # PWA: servidos por Django para que el scope del service worker sea "/".
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="sw"),
    path("offline/", views.offline, name="offline"),
]
