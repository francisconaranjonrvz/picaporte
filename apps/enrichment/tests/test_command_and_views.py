import subprocess
import sys
from pathlib import Path

import pytest

from django.core.management import call_command
from django.urls import reverse

from apps.companies.models import Company
from apps.enrichment import services
from apps.enrichment.models import Enrichment
from apps.jobs.models import JobRun
from apps.profiles.models import Profile

BASE_DIR = Path(__file__).resolve().parents[3]


def test_la_web_no_importa_dependencias_del_worker():
    """Vercel no instala el grupo `worker`: cargar las URLs no puede tirar de bs4 ni duckdb."""
    code = (
        "import os, sys, django;"
        "os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test';"
        "django.setup();"
        "import config.urls;"
        "from django.urls import resolve, reverse;"
        "[resolve(reverse(n)) for n in ('explorar', 'perfil')];"
        "bad = [m for m in ('bs4', 'duckdb', 'apps.enrichment.crawler') if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run(  # noqa: S603 - comando fijo, sin entrada externa
        [sys.executable, "-c", code], cwd=BASE_DIR, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == ""


@pytest.mark.django_db
def test_comando_enrich_registra_jobrun(monkeypatch):
    stats = services.EnrichStats(crawled=2, extracted=1, scored=3)
    stats.crawl_status["ok"] = 1
    received = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return stats

    monkeypatch.setattr("apps.enrichment.management.commands.enrich.run_enrichment", fake_run)
    job = JobRun.objects.create(kind=JobRun.Kind.ENRICH, trigger=JobRun.Trigger.APP)

    call_command("enrich", "--mode", "score", "--limit", "5", "--job-id", str(job.pk))

    job.refresh_from_db()
    assert job.status == JobRun.Status.SUCCESS
    assert job.summary.startswith("webs: 2 rastreadas (1 con contenido)")
    assert job.stats["scored"] == 3
    assert received["mode"] == "score"
    assert received["limit"] == 5


@pytest.mark.django_db
def test_comando_enrich_marca_fallo(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("Neon caído")

    monkeypatch.setattr("apps.enrichment.management.commands.enrich.run_enrichment", boom)
    with pytest.raises(RuntimeError):
        call_command("enrich")
    job = JobRun.objects.get(kind=JobRun.Kind.ENRICH)
    assert job.status == JobRun.Status.FAILED
    assert "Neon caído" in job.error


def test_datos_muestra_el_analisis_y_explorar_ordena_por_encaje(auth_client, user):
    resp = auth_client.get(reverse("datos"))
    assert "Encaje con tu perfil" not in resp.text  # sin empresas no hay tarjeta

    buzz = Company.objects.create(name="Buzz", website="https://buzz.es")
    cowork = Company.objects.create(name="Cowork")
    Enrichment.objects.create(
        company=buzz,
        crawl_status="ok",
        fit_score=82,
        fit_reason="Hace eventos de marca.",
        hook="Vi vuestra campaña para Wallbox.",
        profile_hash="viejo",
        scored_at="2026-09-20T10:00:00Z",
    )
    Enrichment.objects.create(company=cowork, crawl_status="no_website")

    resp = auth_client.get(reverse("datos"))
    assert "Encaje con tu perfil" in resp.text
    assert "sube y analiza el CV" in resp.text  # sin perfil no se puntúa
    assert 'hx-post="/jobs/enrich/run"' in resp.text

    resp = auth_client.get(reverse("explorar"))
    html = resp.text
    assert html.index(">Buzz</h2>") < html.index(">Cowork</h2>")  # primero el mejor encaje
    assert "Hace eventos de marca." in html

    Profile.objects.create(user=user, full_name="Laura", skills=["Canva"])
    resp = auth_client.get(reverse("datos"))
    assert "Tu perfil ha cambiado: 1 empresa con la puntuación anterior." in resp.text
    assert "Solo recalcular el encaje" in resp.text


def test_perfil_ofrece_recalcular_si_el_encaje_esta_desactualizado(auth_client, user):
    Profile.objects.create(user=user, full_name="Laura", skills=["Canva"])
    resp = auth_client.get(reverse("perfil"))
    assert "Tu perfil ha cambiado" not in resp.text

    Enrichment.objects.create(
        company=Company.objects.create(name="Buzz"),
        crawl_status="no_website",
        fit_score=50,
        profile_hash="de-otro-perfil",
        scored_at="2026-09-20T10:00:00Z",
    )
    resp = auth_client.get(reverse("perfil"))
    assert "Tu perfil ha cambiado" in resp.text
    assert "1 empresa se puntuó con tu perfil anterior" in resp.text
    assert 'name="mode" value="score"' in resp.text
