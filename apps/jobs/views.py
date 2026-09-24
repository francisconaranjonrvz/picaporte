import json

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .models import JobRun
from .services import refresh_from_github, start_from_app

ENRICH_MODES = {"all", "score"}


# Descubrir y enriquecer son trabajos de todo el catálogo: solo desde una cuenta de staff.
GLOBAL_KINDS = {JobRun.Kind.DISCOVER, JobRun.Kind.ENRICH}


def latest(kind: str, user=None) -> JobRun | None:
    """Último trabajo de ese tipo; la búsqueda personalizada, la del usuario."""
    jobs = JobRun.objects.filter(kind=kind)
    if kind == JobRun.Kind.PERSONALIZE:
        jobs = jobs.filter(user=user)
    return jobs.first()


def _kind(kind: str) -> str:
    if kind not in JobRun.Kind.values:
        raise Http404("Tipo de trabajo desconocido")
    return kind


@require_POST
def trigger(request, kind):
    """Botones "Buscar nuevas empresas" / "Analizar webs": lanza el workflow (parcial HTMX)."""
    kind = _kind(kind)
    if kind in GLOBAL_KINDS and not request.user.is_staff:
        raise PermissionDenied("Solo una cuenta de administración lanza trabajos globales.")
    inputs = {}
    if kind == JobRun.Kind.ENRICH:
        mode = request.POST.get("mode", "all")
        inputs["mode"] = mode if mode in ENRICH_MODES else "all"
    elif kind == JobRun.Kind.PERSONALIZE:
        inputs["discover"] = "0" if request.POST.get("discover") == "0" else "1"
    user = request.user if kind == JobRun.Kind.PERSONALIZE else None
    job = start_from_app(kind, inputs, user=user)
    # Si otro trabajo lo bloquea, se muestra (y se sondea) ese en lugar del pedido.
    return render(request, "jobs/_status.html", {"job": job, "kind": job.kind})


@require_GET
def status(request, kind):
    """Parcial de estado; HTMX lo sondea cada 10 s mientras el trabajo está activo.

    Cuando el trabajo termina se emite el evento `job-finished`: los botones para lanzar
    trabajos (fuera del parcial) se pintaron deshabilitados. base.html los reactiva, y las
    páginas cuyas cifras cambian (Datos) se recargan; Perfil no, para no perder lo escrito.
    """
    kind = _kind(kind)
    job = latest(kind, request.user)
    if job is not None:
        job = refresh_from_github(job)
    response = render(request, "jobs/_status.html", {"job": job, "kind": kind})
    if request.htmx and (job is None or not job.is_active):
        response["HX-Trigger"] = json.dumps({"job-finished": kind})
    return response
