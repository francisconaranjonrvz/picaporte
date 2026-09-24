"""Búsqueda personalizada: guardar el perfil lanza la búsqueda y la puntuación."""

import pytest

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category
from apps.companies import services as company_services
from apps.companies.models import Company
from apps.companies.relevance import CORE_SECTORS
from apps.core.testing import owner
from apps.enrichment import services as enrichment_services
from apps.enrichment.models import Enrichment, FitScore
from apps.jobs import github
from apps.jobs import services as job_services
from apps.jobs.models import JobRun
from apps.profiles.models import Profile

FORM = {
    "full_name": "Laura Vidal",
    "headline": "Graduada en Publicidad y RRPP",
    "summary": "Me gustan los eventos.",
    "skills": "Canva, Meta Ads",
    "languages": "Catalán · nativo",
    "education": "",
    "experience": "",
    "interests": "",
}


@pytest.fixture
def dispatched(settings, monkeypatch):
    settings.GITHUB_DISPATCH_TOKEN = "t"
    calls = []

    def fake(workflow, inputs):
        calls.append((workflow, inputs))
        return github.DispatchedRun(len(calls), f"https://gh/{len(calls)}")

    monkeypatch.setattr(job_services.github, "dispatch_workflow", fake)
    return calls


def _post(client, **changes):
    return client.post(reverse("profile_update"), {**FORM, **changes}, follow=True)


def _sector_pks(*slugs):
    return [str(pk) for pk in Category.objects.filter(slug__in=slugs).values_list("pk", flat=True)]


def _finish_jobs():
    JobRun.objects.update(status=JobRun.Status.SUCCESS, finished_at=timezone.now())


def test_primer_guardado_busca_y_puntua(auth_client, dispatched):
    resp = _post(auth_client)

    assert "Buscando empresas de tus sectores" in resp.text
    job = JobRun.objects.get()
    assert (job.kind, job.user) == (JobRun.Kind.PERSONALIZE, owner())
    assert dispatched == [
        ("personalize.yml", {"job_id": str(job.pk), "discover": "1", "user_id": str(owner().pk)})
    ]


def test_sin_cambios_no_relanza_y_con_cambios_decide_que_rehacer(auth_client, user, dispatched):
    profile = Profile.objects.create(user=user)
    profile.discovered_sectors = sorted(CORE_SECTORS)  # la última búsqueda ya usó estos
    profile.save()

    _post(auth_client)  # el perfil pasa a tener datos: hay que puntuar, no buscar
    assert dispatched[-1][1]["discover"] == "0"
    _finish_jobs()

    resp = _post(auth_client)  # nada ha cambiado
    assert "Perfil guardado." in resp.text
    assert len(dispatched) == 1

    _post(auth_client, categories=_sector_pks("eventos"))  # sector básico: solo puntuar
    assert dispatched[-1][1]["discover"] == "0"
    _finish_jobs()

    _post(auth_client, categories=_sector_pks("eventos", "medios"))  # opcional nuevo: buscar
    assert dispatched[-1][1]["discover"] == "1"
    assert len(dispatched) == 3


def test_perfil_vacio_no_lanza_nada(auth_client, dispatched):
    _post(auth_client, full_name="", summary="", skills="", headline="")
    assert dispatched == []


def test_sin_token_avisa_sin_perder_el_perfil(auth_client, settings):
    settings.GITHUB_DISPATCH_TOKEN = ""
    resp = _post(auth_client)
    assert "no se pudo lanzar la búsqueda" in resp.text
    assert Profile.objects.get().full_name == "Laura Vidal"


def test_no_se_lanza_un_enriquecimiento_durante_la_busqueda_personalizada(
    auth_client, user, dispatched, monkeypatch
):
    user.is_staff = True
    user.save()
    monkeypatch.setattr(
        job_services.github, "run_state", lambda run_id: github.RunState("in_progress", None, "")
    )
    job = job_services.start_from_app(JobRun.Kind.PERSONALIZE, {"discover": "1"}, user=user)

    resp = auth_client.post(reverse("job_trigger", args=["enrich"]), {"mode": "all"})

    assert len(dispatched) == 1
    assert 'id="job-status-personalize"' in resp.text  # se sondea la que está en marcha
    assert JobRun.objects.get() == job


def test_perfil_muestra_sectores_estado_y_ranking(auth_client, user):
    profile = Profile.objects.create(user=user, full_name="Laura")
    profile.categories.set(Category.objects.filter(slug="medios"))
    for name, score in (("Zeta Baja", 20), ("Alfa Top", 88), ("Beta Media", 55)):
        company = Company.objects.create(name=name)
        Enrichment.objects.create(company=company, crawl_status="no_website")
        FitScore.objects.create(user=user, company=company, fit_score=score)

    text = auth_client.get(reverse("perfil")).text

    assert "Medios: prensa, radio y TV" in text
    assert "Agencias de publicidad" in text  # los básicos siempre
    assert "Buscar y puntuar ahora" in text
    assert text.index("Alfa Top") < text.index("Beta Media") < text.index("Zeta Baja")
    assert "88 · Ve primero" in text


@pytest.mark.django_db
def test_comando_personalize_encadena_busqueda_y_enriquecimiento(monkeypatch, user):
    profile = Profile.objects.create(user=user, full_name="Laura")
    profile.categories.set(Category.objects.filter(slug="medios"))
    seen = {}

    def fake_discovery(source_names=None, **kwargs):
        seen["discover"] = kwargs
        return {"osm": company_services.IngestStats(fetched=3, created=2)}

    monkeypatch.setattr(
        "apps.enrichment.management.commands.personalize.run_discovery", fake_discovery
    )
    monkeypatch.setattr(
        "apps.enrichment.management.commands.personalize.run_enrichment",
        lambda **kw: seen.setdefault("enrich", kw) and enrichment_services.EnrichStats(scored=4),
    )
    job = JobRun.objects.create(kind=JobRun.Kind.PERSONALIZE, trigger=JobRun.Trigger.APP)

    call_command("personalize", "--job-id", str(job.pk), "--user-id", str(user.pk))

    job.refresh_from_db()
    assert job.status == JobRun.Status.SUCCESS
    assert "osm: 3 encontradas, 2 nuevas" in job.summary
    assert (seen["enrich"]["mode"], seen["enrich"]["user"]) == ("all", user)
    assert job.user == user
    assert set(job.stats) == {"discover", "enrich"}

    call_command("personalize", "--skip-discover", "--user-id", str(user.pk))
    assert JobRun.objects.filter(kind=JobRun.Kind.PERSONALIZE).count() == 2
    assert "discover" not in JobRun.objects.order_by("-pk").first().stats


@pytest.mark.django_db
def test_la_busqueda_recuerda_los_sectores_del_perfil(monkeypatch, user):
    profile = Profile.objects.create(user=user, full_name="Laura")
    profile.categories.set(Category.objects.filter(slug__in=["fotografia", "eventos"]))
    monkeypatch.setattr(company_services, "get_adapters", lambda names: [])

    company_services.run_discovery()

    profile.refresh_from_db()
    assert profile.discovered_sectors == sorted(CORE_SECTORS | {"fotografia"})


def test_cupo_diario_de_busquedas_personalizadas(auth_client, user, dispatched):
    for _ in range(job_services.PERSONAL_PER_DAY):
        job_services.start_from_app(JobRun.Kind.PERSONALIZE, {"discover": "0"}, user=user)
        _finish_jobs()
    job = job_services.start_from_app(JobRun.Kind.PERSONALIZE, {"discover": "0"}, user=user)
    assert job.status == JobRun.Status.FAILED
    assert "Límite diario" in job.error
    assert len(dispatched) == job_services.PERSONAL_PER_DAY


def test_la_busqueda_de_otra_cuenta_no_bloquea_la_mia(user, other_user, dispatched, monkeypatch):
    monkeypatch.setattr(
        job_services.github, "run_state", lambda run_id: github.RunState("in_progress", None, "")
    )
    mine = job_services.start_from_app(JobRun.Kind.PERSONALIZE, {"discover": "1"}, user=user)
    theirs = job_services.start_from_app(
        JobRun.Kind.PERSONALIZE, {"discover": "1"}, user=other_user
    )
    assert mine.pk != theirs.pk
    assert [inputs["user_id"] for _, inputs in dispatched] == [str(user.pk), str(other_user.pk)]


@pytest.mark.django_db
def test_se_buscan_los_sectores_de_todas_las_cuentas(monkeypatch, user, other_user):
    Profile.objects.create(user=user).categories.set(Category.objects.filter(slug="medios"))
    Profile.objects.create(user=other_user).categories.set(Category.objects.filter(slug="cultura"))
    monkeypatch.setattr(company_services, "get_adapters", lambda names: [])

    company_services.run_discovery()

    expected = sorted(CORE_SECTORS | {"medios", "cultura"})
    assert list(Profile.objects.values_list("discovered_sectors", flat=True)) == [expected] * 2
