from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # El admin trae su propio login (exento de LoginRequiredMiddleware por diseño).
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.profiles.urls")),
    path("", include("apps.jobs.urls")),
    path("", include("apps.companies.urls")),
    path("", include("apps.tracking.urls")),
    path("", include("apps.routes.urls")),
    path("", include("apps.core.urls")),
]
