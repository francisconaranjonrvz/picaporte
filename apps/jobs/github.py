"""Cliente mínimo de la API de GitHub Actions: disparar un workflow y consultar su estado.

Con la versión de API 2026-03-10, `POST .../dispatches` devuelve directamente
el id del run, así que no hay que buscarlo después. Token: PAT *fine-grained*
con permiso "Actions: write" sobre el repositorio (GITHUB_DISPATCH_TOKEN).
"""

import logging
from dataclasses import dataclass

import httpx

from django.conf import settings

logger = logging.getLogger(__name__)

API = "https://api.github.com"
API_VERSION = "2026-03-10"


class GitHubError(Exception):
    pass


class GitHubNotConfigured(GitHubError):
    pass


@dataclass(frozen=True)
class DispatchedRun:
    run_id: int | None
    html_url: str


@dataclass(frozen=True)
class RunState:
    status: str  # queued | in_progress | completed
    conclusion: str | None  # success | failure | cancelled | ...
    html_url: str


def _headers() -> dict[str, str]:
    if not settings.GITHUB_DISPATCH_TOKEN:
        raise GitHubNotConfigured("Falta GITHUB_DISPATCH_TOKEN: no se puede lanzar el workflow.")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {settings.GITHUB_DISPATCH_TOKEN}",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        response = httpx.request(method, f"{API}{path}", headers=_headers(), timeout=20.0, **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubError(f"No se pudo contactar con GitHub: {exc}") from exc
    if response.status_code == 401:
        raise GitHubError("GitHub rechazó el token (401): revisa GITHUB_DISPATCH_TOKEN.")
    if response.status_code == 403:
        raise GitHubError("Token sin permiso (403): necesita 'Actions: write' en el repositorio.")
    if response.status_code == 404:
        raise GitHubError("Workflow o repositorio no encontrado (404).")
    if response.status_code >= 400:
        raise GitHubError(f"GitHub respondió {response.status_code}: {response.text[:200]}")
    return response


def dispatch_workflow(workflow_file: str, inputs: dict[str, str] | None = None) -> DispatchedRun:
    repo, ref = settings.GITHUB_REPO, settings.GITHUB_WORKFLOW_REF
    response = _request(
        "POST",
        f"/repos/{repo}/actions/workflows/{workflow_file}/dispatches",
        json={"ref": ref, "inputs": inputs or {}},
    )
    if response.status_code == 200:  # API 2026-03-10: detalles del run
        data = response.json()
        return DispatchedRun(data.get("workflow_run_id"), data.get("html_url", ""))
    return DispatchedRun(None, f"https://github.com/{repo}/actions/workflows/{workflow_file}")


def run_state(run_id: int) -> RunState:
    data = _request("GET", f"/repos/{settings.GITHUB_REPO}/actions/runs/{run_id}").json()
    return RunState(data.get("status", ""), data.get("conclusion"), data.get("html_url", ""))
