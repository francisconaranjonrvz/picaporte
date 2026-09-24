"""Casos de uso de los trabajos: lanzarlos desde la app y refrescar su estado desde GitHub."""

import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import github
from .models import JobRun

WORKFLOWS = {
    JobRun.Kind.DISCOVER: "discover.yml",
    JobRun.Kind.ENRICH: "enrich.yml",
    JobRun.Kind.PERSONALIZE: "personalize.yml",
}
GITHUB_CHECK_INTERVAL = timedelta(seconds=15)
# discover.yml corta a los 45 min: un job "activo" más viejo que esto murió sin avisar
# (p. ej. cancelado) y no debe bloquear el botón. La búsqueda personalizada encadena
# descubrimiento y enriquecimiento (personalize.yml corta a los 100 min).
STALE_AFTER = timedelta(hours=1)
STALE_AFTER_BY_KIND = {JobRun.Kind.PERSONALIZE: timedelta(hours=2)}
# Mientras corre la búsqueda personalizada no se lanza otro enriquecimiento: rastrearían
# las mismas webs a la vez.
BLOCKED_BY = {JobRun.Kind.ENRICH: (JobRun.Kind.PERSONALIZE,)}


def _active(kind: str) -> JobRun | None:
    job = JobRun.objects.filter(
        kind=kind, status__in=(JobRun.Status.QUEUED, JobRun.Status.RUNNING)
    ).first()
    if job is not None:
        job = refresh_from_github(job)
    return job if job is not None and job.is_active else None


def start_from_app(kind: str, inputs: dict[str, str] | None = None) -> JobRun:
    """Crea el JobRun y dispara su workflow; el comando en Actions lo continúa por id.

    Si ya hay uno activo del mismo tipo, o uno que lo bloquea (y sigue vivo en GitHub),
    se devuelve ese.
    """
    for other in (kind, *BLOCKED_BY.get(kind, ())):
        if (active := _active(other)) is not None:
            return active
    job = JobRun.objects.create(kind=kind, trigger=JobRun.Trigger.APP)
    try:
        dispatched = github.dispatch_workflow(
            WORKFLOWS[kind], {"job_id": str(job.pk), **(inputs or {})}
        )
    except github.GitHubError as exc:
        job.mark_finished(stats={}, summary="", error=str(exc))
        return job
    job.github_run_id = dispatched.run_id
    job.github_run_url = dispatched.html_url
    job.save(update_fields=["github_run_id", "github_run_url"])
    return job


def attach_github_context(job: JobRun) -> None:
    """Dentro de Actions: guarda el run actual (GITHUB_RUN_ID) en el JobRun."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", settings.GITHUB_REPO)
    job.github_run_id = int(run_id)
    job.github_run_url = f"{server}/{repo}/actions/runs/{run_id}"
    job.save(update_fields=["github_run_id", "github_run_url"])


def begin_job(kind: str, job_id: int | None) -> JobRun:
    """Para los comandos: continúa el JobRun creado por la app o crea uno, y lo marca en marcha."""
    job = JobRun.objects.filter(pk=job_id, kind=kind).first() if job_id else None
    if job is None:
        job = JobRun.objects.create(kind=kind, trigger=trigger_from_env(job_id))
    attach_github_context(job)
    job.mark_running()
    return job


def trigger_from_env(job_id: int | None) -> str:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "schedule":
        return JobRun.Trigger.SCHEDULE
    if job_id is not None:
        return JobRun.Trigger.APP
    return JobRun.Trigger.MANUAL


def refresh_from_github(job: JobRun) -> JobRun:
    """Si el workflow acabó sin que el comando cerrara el JobRun (p. ej. fallo antes de arrancar)."""
    stale_after = STALE_AFTER_BY_KIND.get(job.kind, STALE_AFTER)
    if job.is_active and timezone.now() - job.created_at > stale_after:
        hours = int(stale_after.total_seconds() // 3600)
        job.mark_finished(
            stats={},
            summary="",
            error=f"Sin noticias del workflow en más de {hours} h: se da por perdido.",
        )
        return job
    if not job.is_active or not job.github_run_id or not settings.GITHUB_DISPATCH_TOKEN:
        return job
    if job.github_checked_at and timezone.now() - job.github_checked_at < GITHUB_CHECK_INTERVAL:
        return job
    job.github_checked_at = timezone.now()
    job.save(update_fields=["github_checked_at"])
    try:
        state = github.run_state(job.github_run_id)
    except github.GitHubError:
        return job
    if state.html_url and not job.github_run_url:
        job.github_run_url = state.html_url
        job.save(update_fields=["github_run_url"])
    if state.status == "completed" and state.conclusion != "success":
        job.mark_finished(
            stats={},
            summary="",
            error=f"El workflow terminó con estado '{state.conclusion}' sin registrar resultados.",
        )
    return job
