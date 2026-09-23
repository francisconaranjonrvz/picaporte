import pytest

from django.core.management import CommandError, call_command

from apps.catalog.models import Category, Zone
from apps.companies import services
from apps.companies.models import Company, Source, SourceRecord
from apps.companies.sources import ADAPTERS
from apps.companies.sources.base import RawCompany, SourceError
from apps.jobs.models import JobRun


def _raw(**kw):
    base = {
        "source": Source.OSM,
        "external_id": "node/1",
        "name": "Buzz Agencia",
        "category_slug": "publicidad",
        "lat": 41.3931,  # Eixample
        "lng": 2.1650,
        "website": "https://www.buzzmn.com/",
        "payload": {"tags": {"office": "advertising_agency"}},
    }
    base.update(kw)
    return RawCompany(**base)


@pytest.fixture
def ctx(db):
    return {
        "categories": {c.slug: c for c in Category.objects.all()},
        "zones": list(Zone.objects.all()),
    }


def test_zone_for_elige_la_zona_mas_pequena_que_contiene_el_punto(ctx):
    zones = ctx["zones"]
    assert (
        services.zone_for(41.405, 2.16, zones).slug == "gracia"
    )  # dentro de Gràcia y del Eixample
    assert services.zone_for(41.395, 2.155, zones).slug == "eixample"
    assert services.zone_for(41.40, 2.20, zones).slug == "poblenou"
    assert services.zone_for(41.0, 2.0, zones) is None
    assert services.zone_for(None, None, zones) is None


def test_ingest_crea_actualiza_y_fusiona(ctx):
    company, outcome = services.ingest(_raw(), **ctx)
    assert outcome == "created"
    assert company.category.slug == "publicidad"
    assert company.zone.slug == "eixample"
    assert company.domain == "buzzmn.com"
    assert company.confidence_score == 35 + 10 + 6 + 5  # 1 fuente, web, coords, categoría
    assert SourceRecord.objects.get().external_id == "node/1"

    # Mismo registro otra vez: actualiza, no duplica.
    company2, outcome = services.ingest(_raw(phone="930"), **ctx)
    assert (company2.pk, outcome) == (company.pk, "updated:record")
    assert Company.objects.count() == 1
    assert company2.phone == "930"

    # Otra fuente con el mismo dominio: se fusiona y sube la confianza.
    company3, outcome = services.ingest(
        _raw(source=Source.FOURSQUARE, external_id="fsq/9", name="Buzz Agency"), **ctx
    )
    assert (company3.pk, outcome) == (company.pk, "updated:domain")
    assert SourceRecord.objects.count() == 2
    assert company3.confidence_score > company2.confidence_score
    assert company3.name == "Buzz Agency"  # Foursquare tiene prioridad sobre OSM


def test_ingest_sin_categoria_ni_coordenadas(ctx):
    company, _ = services.ingest(_raw(category_slug=None, lat=None, lng=None, website=""), **ctx)

    assert company.category is None
    assert company.zone is None
    assert company.confidence_score == 35


class FakeAdapter:
    def __init__(self, raws=None, error=None, name="fake"):
        self.raws = raws or []
        self.error = error
        self.name = name

    def fetch(self):
        if self.error:
            raise SourceError(self.error)
        yield from self.raws


def test_run_discovery_aisla_errores_por_fuente(ctx, monkeypatch):
    ok = FakeAdapter([_raw(), _raw(external_id="node/2", name="Otra", website="")])
    ko = FakeAdapter(error="Overpass caído")
    monkeypatch.setattr(services, "get_adapters", lambda names: [ok, ko])

    results = services.run_discovery(["fake"])

    assert results["fake"].error == "Overpass caído"  # el último con ese nombre
    assert Company.objects.count() == 2


def test_run_discovery_dry_run_no_escribe(ctx, monkeypatch):
    monkeypatch.setattr(services, "get_adapters", lambda names: [FakeAdapter([_raw()])])

    results = services.run_discovery(None, dry_run=True)

    assert results["fake"].fetched == 1
    assert Company.objects.count() == 0


@pytest.mark.django_db
def test_comando_discover_registra_jobrun(monkeypatch):
    monkeypatch.setattr(services, "get_adapters", lambda names: [FakeAdapter([_raw()])])
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_REPOSITORY", "francisconaranjonrvz/picaporte")

    call_command("discover")

    job = JobRun.objects.get()
    assert job.status == JobRun.Status.SUCCESS
    assert job.trigger == JobRun.Trigger.SCHEDULE
    assert job.github_run_id == 123456
    assert job.github_run_url.endswith("/actions/runs/123456")
    assert job.stats["fake"]["created"] == 1
    assert "1 nuevas" in job.summary
    assert job.duration_seconds is not None


@pytest.mark.django_db
def test_comando_discover_continua_el_job_de_la_app_y_marca_fallo(monkeypatch):
    job = JobRun.objects.create(kind=JobRun.Kind.DISCOVER, trigger=JobRun.Trigger.APP)
    monkeypatch.setattr(services, "get_adapters", lambda names: [FakeAdapter(error="sin red")])

    with pytest.raises(CommandError, match="Todas las fuentes"):
        call_command("discover", job_id=job.pk)

    job.refresh_from_db()
    assert job.status == JobRun.Status.FAILED
    assert "sin red" in job.error
    assert JobRun.objects.count() == 1


def test_comando_rechaza_fuentes_desconocidas(db):
    with pytest.raises(CommandError, match="desconocidas"):
        call_command("discover", sources="osm,google")
    assert "osm" in ADAPTERS


def test_ingesta_por_lotes_no_consulta_por_empresa(ctx, django_assert_max_num_queries):
    # Empresas previas con categoría y zona: leerlas no debe disparar consultas por fila.
    for i in range(5):
        services.ingest(_raw(external_id=f"node/{i}", name=f"Previa {i}", website=""), **ctx)
    raws = [
        _raw(
            source=Source.FOURSQUARE,
            external_id=f"fsq/{i}",
            name=f"Agencia {i}",
            website=f"https://agencia{i}.example",
            lat=41.39 + i * 0.001,
        )
        for i in range(60)
    ]
    # Mismo lugar dos veces en el lote: la segunda debe fusionarse con la primera (aún sin guardar).
    raws.append(_raw(source=Source.OSM, external_id="node/x", website="https://agencia0.example"))

    # 2 cargas + 3 volcados (savepoint, inserts, update): constante, no crece con las filas.
    with django_assert_max_num_queries(16):
        ingestor = services.Ingestor(flush_every=25, **ctx)
        outcomes = [ingestor.add(raw)[1] for raw in raws]
        ingestor.flush()

    assert outcomes.count("created") == 60
    assert outcomes[-1] == "updated:domain"
    assert Company.objects.count() == 65
    merged = Company.objects.get(domain="agencia0.example")
    assert set(merged.records.values_list("source", flat=True)) == {"foursquare", "osm"}
    assert merged.confidence_score == services.confidence_score(merged, 2)


def test_run_discovery_retira_lo_que_la_fuente_ya_no_devuelve(ctx, monkeypatch):
    solo_fsq = _raw(source=Source.FOURSQUARE, external_id="fsq/1", name="Cerrada", website="")
    compartida_osm = _raw(external_id="node/2", name="Compartida", website="https://c.example")
    compartida_fsq = _raw(
        source=Source.FOURSQUARE, external_id="fsq/2", website="https://c.example"
    )
    sigue = _raw(
        source=Source.FOURSQUARE, external_id="fsq/3", name="Sigue", website="https://s.ex"
    )
    for raw in (solo_fsq, compartida_osm, compartida_fsq, sigue):
        services.ingest(raw, **ctx)

    adapter = FakeAdapter([sigue], name=Source.FOURSQUARE)
    monkeypatch.setattr(services, "get_adapters", lambda names: [adapter])
    results = services.run_discovery(["foursquare"])

    assert results["foursquare"].retired == 2
    assert not Company.objects.get(name="Cerrada").is_active  # sin fuentes: inactiva, no borrada
    compartida = Company.objects.get(domain="c.example")
    assert compartida.is_active  # le queda OSM
    assert compartida.source_names == ["osm"]
    assert compartida.confidence_score == services.confidence_score(compartida, 1)
    assert Company.objects.get(domain="s.ex").is_active


def test_fuente_con_error_o_vacia_no_retira_nada(ctx, monkeypatch):
    services.ingest(_raw(source=Source.FOURSQUARE, external_id="fsq/1"), **ctx)
    for adapter in (
        FakeAdapter(error="caída", name=Source.FOURSQUARE),
        FakeAdapter([], name=Source.FOURSQUARE),
    ):
        monkeypatch.setattr(services, "get_adapters", lambda names, a=adapter: [a])
        results = services.run_discovery(None)
        assert results["foursquare"].retired == 0
    assert SourceRecord.objects.count() == 1


def test_empresa_retirada_que_reaparece_vuelve_a_estar_activa(ctx):
    company, _ = services.ingest(_raw(), **ctx)
    Company.objects.filter(pk=company.pk).update(is_active=False)
    company, outcome = services.ingest(_raw(), **ctx)
    assert outcome == "updated:record"
    assert Company.objects.get(pk=company.pk).is_active
