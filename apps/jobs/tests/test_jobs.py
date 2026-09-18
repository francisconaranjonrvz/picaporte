from datetime import timedelta

import httpx
import pytest

from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company
from apps.jobs import github, services
from apps.jobs.models import JobRun


@pytest.fixture
def token(settings):
    settings.GITHUB_DISPATCH_TOKEN = "github_pat_test"
    settings.GITHUB_REPO = "francisconaranjonrvz/picaporte"
    settings.GITHUB_WORKFLOW_REF = "main"


def _fake_request(monkeypatch, responses):
    calls = []

    def fake(method, url, headers=None, timeout=None, **kwargs):
        calls.append((method, url, headers, kwargs))
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(github.httpx, "request", fake)
    return calls


def test_dispatch_devuelve_el_run_con_la_api_2026(token, monkeypatch):
    calls = _fake_request(
        monkeypatch, [(200, {"workflow_run_id": 42, "html_url": "https://github.com/x/runs/42"})]
    )

    run = github.dispatch_workflow("discover.yml", {"job_id": "7"})

    assert run == github.DispatchedRun(42, "https://github.com/x/runs/42")
    method, url, headers, kwargs = calls[0]
    assert (method, url) == (
        "POST",
        f"{github.API}/repos/francisconaranjonrvz/picaporte/actions/workflows/discover.yml/dispatches",
    )
    assert headers["Authorization"] == "Bearer github_pat_test"
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert kwargs["json"] == {"ref": "main", "inputs": {"job_id": "7"}}


def test_dispatch_204_sin_detalles(token, monkeypatch):
    _fake_request(monkeypatch, [(204, None)])

    run = github.dispatch_workflow("discover.yml")

    assert run.run_id is None
    assert run.html_url.endswith("/actions/workflows/discover.yml")


@pytest.mark.parametrize(
    ("status", "fragmento"),
    [(401, "token"), (403, "Actions: write"), (404, "no encontrado"), (500, "500")],
)
def test_errores_http_amigables(token, monkeypatch, status, fragmento):
    _fake_request(monkeypatch, [(status, {"message": "x"})])

    with pytest.raises(github.GitHubError, match=fragmento):
        github.dispatch_workflow("discover.yml")


def test_sin_token_y_sin_red(settings, monkeypatch):
    settings.GITHUB_DISPATCH_TOKEN = ""
    with pytest.raises(github.GitHubNotConfigured):
        github.dispatch_workflow("discover.yml")

    settings.GITHUB_DISPATCH_TOKEN = "t"
    _fake_request(monkeypatch, [httpx.ConnectError("boom")])
    with pytest.raises(github.GitHubError, match="contactar"):
        github.run_state(1)


@pytest.mark.django_db
def test_start_discover_crea_job_y_reusa_el_activo(token, monkeypatch):
    monkeypatch.setattr(
        services.github,
        "dispatch_workflow",
        lambda wf, inputs: github.DispatchedRun(99, "https://gh/99"),
    )

    job = services.start_discover_from_app()
    again = services.start_discover_from_app()

    assert job.status == JobRun.Status.QUEUED
    assert job.trigger == JobRun.Trigger.APP
    assert (job.github_run_id, job.github_run_url) == (99, "https://gh/99")
    assert again.pk == job.pk  # no se lanza otro mientras uno está activo
    assert JobRun.objects.count() == 1


@pytest.mark.django_db
def test_start_discover_sin_token_deja_el_job_fallido(settings):
    settings.GITHUB_DISPATCH_TOKEN = ""

    job = services.start_discover_from_app()

    assert job.status == JobRun.Status.FAILED
    assert "GITHUB_DISPATCH_TOKEN" in job.error


@pytest.mark.django_db
def test_refresh_from_github_cierra_jobs_huerfanos(token, monkeypatch):
    job = JobRun.objects.create(kind=JobRun.Kind.DISCOVER, github_run_id=5)
    states = [
        github.RunState("in_progress", None, "https://gh/5"),
        github.RunState("completed", "failure", "https://gh/5"),
    ]
    monkeypatch.setattr(services.github, "run_state", lambda run_id: states.pop(0))

    job = services.refresh_from_github(job)
    assert job.status == JobRun.Status.QUEUED
    assert job.github_run_url == "https://gh/5"

    # Antes de 15 s no vuelve a consultar; después sí, y cierra el job como fallido.
    job = services.refresh_from_github(job)
    assert len(states) == 1
    JobRun.objects.filter(pk=job.pk).update(
        github_checked_at=timezone.now() - timedelta(seconds=20)
    )
    job = services.refresh_from_github(JobRun.objects.get(pk=job.pk))
    assert job.status == JobRun.Status.FAILED
    assert "failure" in job.error


def test_boton_y_estado_por_htmx(auth_client, token, monkeypatch):
    monkeypatch.setattr(
        services.github,
        "dispatch_workflow",
        lambda wf, inputs: github.DispatchedRun(1, "https://gh/1"),
    )

    resp = auth_client.post(reverse("job_trigger_discover"))

    assert resp.status_code == 200
    assert "En cola en GitHub Actions" in resp.text
    assert 'hx-trigger="every 10s"' in resp.text
    assert "https://gh/1" in resp.text

    JobRun.objects.update(
        status=JobRun.Status.SUCCESS, finished_at=timezone.now(), summary="osm: 3 nuevas"
    )
    resp = auth_client.get(reverse("job_status"))
    assert "hx-trigger" not in resp.text
    assert "osm: 3 nuevas" in resp.text


def test_explorar_muestra_cifras_y_estado(auth_client, db):
    resp = auth_client.get(reverse("explorar"))
    assert "Aún no hay empresas" in resp.text
    assert "Todavía no se ha buscado" in resp.text

    Company.objects.create(name="Buzz", website="https://buzz.com")
    Company.objects.create(name="Cowork")
    resp = auth_client.get(reverse("explorar"))
    assert "2 empresas" in resp.text
    assert "Sin categoría" in resp.text
    assert "1 con web" in resp.text


def test_las_rutas_de_jobs_requieren_login(client, db):
    assert client.post(reverse("job_trigger_discover")).status_code == 302
    assert client.get(reverse("job_status")).status_code == 302
