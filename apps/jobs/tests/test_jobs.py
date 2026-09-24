import json
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

    monkeypatch.setattr(
        services.github, "run_state", lambda run_id: github.RunState("in_progress", None, "")
    )

    job = services.start_from_app(JobRun.Kind.DISCOVER)
    again = services.start_from_app(JobRun.Kind.DISCOVER)

    assert job.status == JobRun.Status.QUEUED
    assert job.trigger == JobRun.Trigger.APP
    assert (job.github_run_id, job.github_run_url) == (99, "https://gh/99")
    assert again.pk == job.pk  # no se lanza otro mientras uno está activo
    assert JobRun.objects.count() == 1


@pytest.mark.django_db
def test_start_discover_no_se_bloquea_por_un_job_muerto(token, monkeypatch):
    """Un job que sigue "activo" pero cuyo run ya terminó (o es muy viejo) no bloquea el botón."""
    monkeypatch.setattr(
        services.github,
        "dispatch_workflow",
        lambda wf, inputs: github.DispatchedRun(7, "https://gh/7"),
    )
    monkeypatch.setattr(
        services.github, "run_state", lambda run_id: github.RunState("completed", "cancelled", "")
    )
    cancelled = JobRun.objects.create(
        kind=JobRun.Kind.DISCOVER, status=JobRun.Status.RUNNING, github_run_id=5
    )
    ancient = JobRun.objects.create(
        kind=JobRun.Kind.DISCOVER,
        status=JobRun.Status.RUNNING,
        created_at=timezone.now() - timedelta(hours=2),
    )

    # El activo más reciente está cancelado en GitHub: se cierra y se lanza uno nuevo.
    job = services.start_from_app(JobRun.Kind.DISCOVER)

    cancelled.refresh_from_db()
    assert cancelled.status == JobRun.Status.FAILED
    assert "cancelled" in cancelled.error
    assert job.github_run_id == 7

    ancient = services.refresh_from_github(ancient)
    assert ancient.status == JobRun.Status.FAILED
    assert "1 h" in ancient.error


@pytest.mark.django_db
def test_start_discover_sin_token_deja_el_job_fallido(settings):
    settings.GITHUB_DISPATCH_TOKEN = ""

    job = services.start_from_app(JobRun.Kind.DISCOVER)

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


def test_boton_y_estado_por_htmx(auth_client, user, token, monkeypatch):
    user.is_staff = True
    user.save()
    monkeypatch.setattr(
        services.github,
        "dispatch_workflow",
        lambda wf, inputs: github.DispatchedRun(1, "https://gh/1"),
    )

    resp = auth_client.post(reverse("job_trigger", args=["discover"]))

    assert resp.status_code == 200
    assert "En cola en GitHub Actions" in resp.text
    assert 'hx-trigger="every 10s"' in resp.text
    assert "https://gh/1" in resp.text

    JobRun.objects.update(
        status=JobRun.Status.SUCCESS, finished_at=timezone.now(), summary="osm: 3 nuevas"
    )
    resp = auth_client.get(reverse("job_status", args=["discover"]))
    assert "hx-trigger" not in resp.text
    assert "osm: 3 nuevas" in resp.text


def test_datos_muestra_cifras_y_estado(auth_client, db):
    assert "Aún no hay empresas" in auth_client.get(reverse("explorar")).text
    resp = auth_client.get(reverse("datos"))
    assert "Todavía no se ha buscado" in resp.text

    buzz = Company.objects.create(name="Buzz", website="https://buzz.com")
    Company.objects.create(name="Cowork")
    Company.objects.create(name="Retirada", is_active=False)
    for source, external_id in (("osm", "node/1"), ("foursquare", "fsq/1")):
        buzz.records.create(source=source, external_id=external_id, name="Buzz")
    resp = auth_client.get(reverse("datos"))
    assert "2 empresas" in resp.text  # la inactiva no cuenta
    assert "Sin categoría" in resp.text
    assert "1 con web" in resp.text
    assert "OpenStreetMap 1" in resp.text
    assert "Foursquare OS Places 1" in resp.text


def test_las_rutas_de_jobs_requieren_login(client, db):
    assert client.post(reverse("job_trigger", args=["discover"])).status_code == 302
    assert client.get(reverse("job_status", args=["discover"])).status_code == 302


def test_enriquecimiento_desde_la_app_pasa_el_modo(auth_client, user, token, monkeypatch):
    user.is_staff = True
    user.save()
    dispatched = []

    def fake_dispatch(workflow, inputs):
        dispatched.append((workflow, inputs))
        return github.DispatchedRun(2, "https://gh/2")

    monkeypatch.setattr(services.github, "dispatch_workflow", fake_dispatch)

    resp = auth_client.post(reverse("job_trigger", args=["enrich"]), {"mode": "score"})
    assert resp.status_code == 200
    assert 'id="job-status-enrich"' in resp.text
    job = JobRun.objects.get()
    assert job.kind == JobRun.Kind.ENRICH
    assert dispatched == [("enrich.yml", {"job_id": str(job.pk), "mode": "score"})]

    # Un modo desconocido se normaliza; mientras hay uno activo no se lanza otro.
    auth_client.post(reverse("job_trigger", args=["enrich"]), {"mode": "rm -rf"})
    assert len(dispatched) == 1

    JobRun.objects.update(status=JobRun.Status.SUCCESS, finished_at=timezone.now())
    auth_client.post(reverse("job_trigger", args=["enrich"]), {"mode": "rm -rf"})
    assert dispatched[-1][1]["mode"] == "all"


def test_tipo_de_trabajo_desconocido_es_404(auth_client):
    assert auth_client.post(reverse("job_trigger", args=["borrar"])).status_code == 404
    assert auth_client.get(reverse("job_status", args=["borrar"])).status_code == 404


def test_al_terminar_el_trabajo_el_sondeo_avisa_a_la_pagina(auth_client, db):
    """Los botones de /datos/ y Perfil se pintan deshabilitados mientras hay un trabajo activo.

    Se avisa con un evento (no HX-Refresh) para que Perfil no pierda lo que se está escribiendo.
    """
    job = JobRun.objects.create(kind=JobRun.Kind.ENRICH, status=JobRun.Status.RUNNING)
    url = reverse("job_status", args=["enrich"])

    resp = auth_client.get(url, HTTP_HX_REQUEST="true")
    assert 'hx-trigger="every 10s"' in resp.text
    assert "HX-Trigger" not in resp

    job.status = JobRun.Status.SUCCESS
    job.finished_at = timezone.now()
    job.save()
    resp = auth_client.get(url, HTTP_HX_REQUEST="true")
    assert json.loads(resp["HX-Trigger"]) == {"job-finished": "enrich"}
    assert "HX-Refresh" not in resp
    assert "data-reload-on-job-finished" in auth_client.get(reverse("datos")).text


def test_los_trabajos_globales_solo_los_lanza_staff(auth_client, token, monkeypatch):
    monkeypatch.setattr(
        services.github, "dispatch_workflow", lambda wf, inputs: pytest.fail("no debe lanzar")
    )
    for kind in ("discover", "enrich"):
        assert auth_client.post(reverse("job_trigger", args=[kind])).status_code == 403
    assert not JobRun.objects.exists()
