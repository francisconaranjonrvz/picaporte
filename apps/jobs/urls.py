from django.urls import path

from . import views

urlpatterns = [
    path("jobs/discover", views.trigger_discover, name="job_trigger_discover"),
    path("jobs/status", views.discover_status, name="job_status"),
]
