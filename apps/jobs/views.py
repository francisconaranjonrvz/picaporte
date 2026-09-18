from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import JobRun
from .services import refresh_from_github, start_discover_from_app


def latest_discover() -> JobRun | None:
    return JobRun.objects.filter(kind=JobRun.Kind.DISCOVER).first()


@require_POST
def trigger_discover(request):
    """Botón "Buscar nuevas empresas": lanza el workflow y devuelve el estado (parcial HTMX)."""
    job = start_discover_from_app()
    return render(request, "jobs/_status.html", {"job": job})


@require_GET
def discover_status(request):
    """Parcial de estado; HTMX lo sondea cada 10 s mientras el trabajo está activo."""
    job = latest_discover()
    if job is not None:
        job = refresh_from_github(job)
    return render(request, "jobs/_status.html", {"job": job})
