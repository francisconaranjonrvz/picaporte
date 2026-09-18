from django.urls import path

from . import views

urlpatterns = [
    path("perfil/", views.perfil, name="perfil"),
    path("perfil/guardar", views.profile_update, name="profile_update"),
    path("perfil/cv", views.cv_upload, name="cv_upload"),
    path("perfil/cv/descargar", views.cv_download, name="cv_download"),
    path("perfil/cv/borrar", views.cv_delete, name="cv_delete"),
    path("perfil/cv/analizar", views.cv_parse, name="cv_parse"),
]
