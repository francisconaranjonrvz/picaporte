from django.urls import path

from . import views

urlpatterns = [
    path("jobs/<slug:kind>/run", views.trigger, name="job_trigger"),
    path("jobs/<slug:kind>/status", views.status, name="job_status"),
]
