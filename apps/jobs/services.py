"""Casos de uso de los trabajos: lanzarlos desde la app y refrescar su estado desde GitHub."""

import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import github
from .models import JobRun

DISCOVER_WORKFLOW = "discover.yml"
GITHUB_CHECK_INTERVAL = timedelta(seconds=15)


def start_discover_from_app() -> JobRun:
    """Crea el JobRun y dispara el workflow; el comando en Actions lo continúa por id."""
    active = JobRun.objects.filter(
        kind=JobRun.Kind.DISCOVER, status__in=(JobRun.Status.QUEUED, JobRun.Status.RUNNING)
    ).first()
    if active is not None:
        return active
    job = JobRun.objects.create(kind=JobRun.Kind.DISCOVER, trigger=JobRun.Trigger.APP)
    try:
        dispatched = github.dispatch_workflow(DISCOVER_WORKFLOW, {"job_id": str(job.pk)})
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


def trigger_from_env(job_id: int | None) -> str:
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if event == "schedule":
        return JobRun.Trigger.SCHEDULE
    if job_id is not None:
        return JobRun.Trigger.APP
    return JobRun.Trigger.MANUAL


def refresh_from_github(job: JobRun) -> JobRun:
    """Si el workflow acabó sin que el comando cerrara el JobRun (p. ej. fallo antes de arrancar)."""
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
