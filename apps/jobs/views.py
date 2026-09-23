from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import JobRun
from .services import refresh_from_github, start_from_app

ENRICH_MODES = {"all", "score"}


def latest(kind: str) -> JobRun | None:
    return JobRun.objects.filter(kind=kind).first()


def _kind(kind: str) -> str:
    if kind not in JobRun.Kind.values:
        raise Http404("Tipo de trabajo desconocido")
    return kind


@require_POST
def trigger(request, kind):
    """Botones "Buscar nuevas empresas" / "Analizar webs": lanza el workflow (parcial HTMX)."""
    kind = _kind(kind)
    inputs = {}
    if kind == JobRun.Kind.ENRICH:
        mode = request.POST.get("mode", "all")
        inputs["mode"] = mode if mode in ENRICH_MODES else "all"
    job = start_from_app(kind, inputs)
    return render(request, "jobs/_status.html", {"job": job, "kind": kind})


@require_GET
def status(request, kind):
    """Parcial de estado; HTMX lo sondea cada 10 s mientras el trabajo está activo."""
    kind = _kind(kind)
    job = latest(kind)
    if job is not None:
        job = refresh_from_github(job)
    return render(request, "jobs/_status.html", {"job": job, "kind": kind})
