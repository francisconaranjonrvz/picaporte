"""Regresiones de la revisión del descubrimiento (dedupe, fusión, retirada y fuentes)."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from django.contrib.admin.sites import AdminSite
from django.utils import timezone

from apps.catalog.models import Category, Zone
from apps.companies import http, services
from apps.companies.admin import CompanyAdmin
from apps.companies.dedupe import (
    find_match,
    merge_into,
    name_similarity,
    normalize_domain,
    normalize_website,
)
from apps.companies.models import Company, FetchCache, Source, SourceRecord
from apps.companies.sources.base import RawCompany, SourceError
from apps.companies.sources.overpass import OVERPASS_URL, QUERY, OverpassAdapter


def _raw(**kw):
    base = {
        "source": Source.OSM,
        "external_id": "node/1",
        "name": "Buzz Agencia",
        "category_slug": "publicidad",
        "lat": 41.3931,
        "lng": 2.1650,
        "website": "https://www.buzzmn.com/",
    }
    base.update(kw)
    return RawCompany(**base)


class FakeAdapter:
    def __init__(self, raws, name=Source.OSM):
        self.raws = raws
        self.name = name

    def fetch(self):
        yield from self.raws


@pytest.fixture
def ctx(db):
    return {
        "categories": {c.slug: c for c in Category.objects.all()},
        "zones": list(Zone.objects.all()),
    }


def _discover(monkeypatch, *adapters):
    monkeypatch.setattr(services, "get_adapters", lambda names: list(adapters))
    return services.run_discovery(None)


# 0: retirar un registro no quita la fuente si la empresa tiene otro de la misma fuente.
def test_retirar_un_registro_no_desactiva_si_queda_otro_de_la_misma_fuente(ctx, monkeypatch):
    a = _raw(external_id="node/1")
    b = _raw(external_id="way/99", lat=41.3932)  # mismo sitio redibujado: se fusiona
    ingestor = services.Ingestor(**ctx)
    ingestor.add(a)
    ingestor.add(b)
    ingestor.flush()
    assert Company.objects.count() == 1

    results = _discover(monkeypatch, FakeAdapter([b]))

    assert results["osm"].retired == 1
    company = Company.objects.get()
    assert company.is_active
    assert company.source_names == ["osm"]
    assert company.confidence_score == services.confidence_score(company, 1)


# 1: mismo dominio a 1 km es otra sede, no la misma empresa.
def test_mismo_dominio_lejos_crea_otra_empresa(ctx):
    ingestor = services.Ingestor(**ctx)
    _, first = ingestor.add(_raw(external_id="node/1", name="Cloudworks Born"))
    _, second = ingestor.add(_raw(external_id="node/2", name="Cloudworks 22@", lat=41.4021))
    _, third = ingestor.add(
        _raw(source=Source.FOURSQUARE, external_id="fsq/1", name="Cloudworks", lat=41.3934)
    )
    ingestor.flush()

    assert (first, second, third) == ("created", "created", "updated:domain")
    assert Company.objects.filter(domain="buzzmn.com").count() == 2


# 2: un perfil de Facebook no es un dominio de empresa.
def test_webs_en_hosts_compartidos_no_fusionan(ctx):
    ingestor = services.Ingestor(**ctx)
    ingestor.add(
        _raw(external_id="node/1", name="Estudio Kabuki", website="https://www.facebook.com/kabuki")
    )
    _, outcome = ingestor.add(
        _raw(
            external_id="node/2",
            name="Zeta Eventos",
            category_slug="eventos",
            website="https://facebook.com/zetaeventos",
            lat=41.3932,
        )
    )
    ingestor.flush()

    assert outcome == "created"
    assert set(Company.objects.values_list("domain", flat=True)) == {""}
    assert Company.objects.get(name="Estudio Kabuki").category.slug == "publicidad"


# 3 y 33: la web se guarda solo si es http(s); sin esquema se le antepone https://.
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://foo.com/a?b=1", "https://foo.com/a?b=1"),
        ("  www.tradel-barcelona.com ", "https://www.tradel-barcelona.com"),
        ("foo.com:8080/x", "https://foo.com:8080/x"),
        ("javascript:fetch('/perfil/'+document.cookie)", ""),
        ("JavaScript:alert(1)", ""),
        ("java\nscript:alert(1)", ""),
        ("mailto:hola@foo.com", ""),
        ("ftp://foo.com", ""),
        ("http://[roto.com", ""),
        ("https://", ""),
        ("https://foo.com/" + "a" * 300, ""),
        ("", ""),
    ],
)
def test_normalize_website(url, expected):
    assert normalize_website(url) == expected


def test_merge_guarda_la_web_normalizada():
    company = Company(name="X")
    merge_into(company, _raw(website="javascript:alert(1)"))
    assert (company.website, company.domain) == ("", "")
    merge_into(company, _raw(website="www.tradel-barcelona.com"))
    assert company.website == "https://www.tradel-barcelona.com"
    assert company.domain == "tradel-barcelona.com"


# 4 y 34: datos anómalos no rompen la ingesta.
def test_normalize_domain_con_url_rota_no_lanza():
    assert normalize_domain("http://[estudioraro.com") == ""


def test_merge_recorta_a_la_longitud_de_cada_campo():
    company = Company(name="X")
    merge_into(
        company,
        _raw(
            name="N" * 250,
            phone="+34 932 000 000;+34 932 000 001;+34 600 000 000",
            postcode="08005 Barcelona",
            address="A" * 400,
        ),
    )
    assert company.phone == "+34 932 000 000"
    assert company.postcode == "08005"
    assert len(company.address) == 255
    assert len(company.name) == 200
    company2 = Company(name="Y")
    merge_into(company2, _raw(postcode="08001;08002"))
    assert company2.postcode == "08001"


def test_un_registro_que_falla_se_omite_y_siguen_las_demas_fuentes(ctx, monkeypatch):
    original = services.merge_into

    def merge_that_fails(company, raw, category=None):
        if raw.name == "Rompe":
            raise ValueError("dato raro")
        return original(company, raw, category=category)

    monkeypatch.setattr(services, "merge_into", merge_that_fails)
    osm = FakeAdapter([_raw(external_id="node/1", name="Rompe"), _raw(external_id="node/2")])
    fsq = FakeAdapter(
        [_raw(source=Source.FOURSQUARE, external_id="fsq/1", name="Otra", website="")],
        name=Source.FOURSQUARE,
    )

    results = _discover(monkeypatch, osm, fsq)

    assert (results["osm"].skipped, results["osm"].created) == (1, 1)
    assert results["foursquare"].created == 1
    assert set(Company.objects.values_list("name", flat=True)) == {"Buzz Agencia", "Otra"}


# 5: un 200 con 'remark' de error es una lectura incompleta y no se queda en caché.
def test_overpass_con_remark_de_error_falla_y_se_borra_de_la_cache(db):
    key = http._key("POST", OVERPASS_URL, http._body({"data": QUERY}))
    body = {
        "remark": "runtime error: Query timed out in 'query' at line 3 after 180 seconds.",
        "elements": [{"type": "node", "id": 1, "lat": 41.39, "lon": 2.16, "tags": {"name": "A"}}],
    }
    FetchCache.objects.create(
        key=key,
        url=OVERPASS_URL,
        status_code=200,
        body=json.dumps(body),
        expires_at=timezone.now() + timedelta(days=7),
    )

    with pytest.raises(SourceError, match="incompleto"):
        list(OverpassAdapter().fetch())
    assert not FetchCache.objects.filter(key=key).exists()


# 6: al retirar la fuente dueña de un campo, la que queda puede actualizarlo.
def test_retirar_una_fuente_libera_sus_campos(ctx, monkeypatch):
    fsq = _raw(source=Source.FOURSQUARE, external_id="fsq/1", name="Buzz FSQ", address="Vieja 1")
    osm = _raw(external_id="node/1", name="Buzz OSM", address="Vieja 1")
    for raw in (fsq, osm):
        services.ingest(raw, **ctx)
    company = Company.objects.get()
    assert company.field_sources["address"] == Source.FOURSQUARE

    otra = _raw(source=Source.FOURSQUARE, external_id="fsq/2", name="Otra", website="", lat=41.40)
    _discover(monkeypatch, FakeAdapter([otra], name=Source.FOURSQUARE))  # fsq/1 ya no está
    assert not SourceRecord.objects.filter(external_id="fsq/1").exists()
    company.refresh_from_db()
    assert Source.FOURSQUARE not in company.field_sources.values()

    moved = _raw(external_id="node/1", name="Buzz Nuevo", address="Nueva 2", lat=41.3935)
    _discover(monkeypatch, FakeAdapter([moved]))
    company.refresh_from_db()
    assert (company.name, company.address, company.lat) == ("Buzz Nuevo", "Nueva 2", 41.3935)


# 7: un nombre hecho solo de palabras genéricas no fusiona por nombre.
def test_nombres_genericos_no_casan_por_nombre():
    assert name_similarity("Events", "Kiwi Events") == 0
    assert name_similarity("Coworking Barcelona", "Aticco Coworking") == 0
    assert name_similarity("Publicitat Barcelona", "Publicitat Roca") == 0
    assert name_similarity("Estudi Cactus", "Estudio Cactus S.L.") >= 90
    assert name_similarity("Impact Hub Barcelona", "Impact Hub") >= 90


@pytest.mark.django_db
def test_find_match_no_absorbe_con_un_nombre_generico():
    Company.objects.create(name="Events", lat=41.3931, lng=2.1650)
    assert find_match(_raw(name="Kiwi Events", website="")) == (None, "")


# 8: lo editado en el admin no lo pisa el siguiente descubrimiento.
def test_ediciones_del_admin_sobreviven_al_descubrimiento(ctx, rf):
    company, _ = services.ingest(_raw(phone="930"), **ctx)
    company.name = "Buzz (corregido)"
    company.phone = "931"
    company.is_active = False
    form = SimpleNamespace(changed_data=["name", "phone", "is_active"])
    CompanyAdmin(Company, AdminSite()).save_model(rf.post("/"), company, form, True)
    assert company.field_sources["phone"] == Source.MANUAL

    services.ingest(_raw(phone="930", name="Buzz Agencia"), **ctx)

    company.refresh_from_db()
    assert (company.name, company.phone, company.is_active) == ("Buzz (corregido)", "931", False)


@pytest.mark.django_db
def test_migracion_normaliza_las_webs_guardadas():
    from importlib import import_module

    from django.apps import apps as django_apps

    migration = import_module("apps.companies.migrations.0003_normalize_websites")
    xss = Company.objects.create(name="A", website="javascript:alert(1)", domain="javascript")
    bare = Company.objects.create(name="B", website="www.tradel-barcelona.com", domain="x")
    social = Company.objects.create(
        name="C", website="https://facebook.com/c", domain="facebook.com"
    )

    migration.normalize_websites(django_apps, None)

    for company in (xss, bare, social):
        company.refresh_from_db()
    assert (xss.website, xss.domain) == ("", "")
    assert (bare.website, bare.domain) == (
        "https://www.tradel-barcelona.com",
        "tradel-barcelona.com",
    )
    assert (social.website, social.domain) == ("https://facebook.com/c", "")


@pytest.mark.django_db
def test_migracion_suelta_las_sedes_fusionadas():
    from importlib import import_module

    from django.apps import apps as django_apps

    from apps.companies.models import Company, SourceRecord

    cadena = Company.objects.create(name="Cloudworks", domain="wearecloudworks.com")
    for i in range(3):
        cadena.records.create(source="osm", external_id=f"node/{i}", name=f"Cloudworks {i}")
    cadena.records.create(source="foursquare", external_id="fsq/1", name="Cloudworks")
    sola = Company.objects.create(name="Sol")
    sola.records.create(source="osm", external_id="node/9", name="Sol")

    migration = import_module("apps.companies.migrations.0004_unmerge_chain_records")
    migration.unmerge(django_apps, None)

    assert sorted(cadena.records.values_list("external_id", flat=True)) == ["fsq/1", "node/0"]
    assert SourceRecord.objects.filter(company=sola).count() == 1
