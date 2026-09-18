import pytest

from apps.catalog.models import Category
from apps.companies.dedupe import (
    confidence_score,
    distance_m,
    find_match,
    merge_into,
    name_similarity,
    normalize_domain,
    normalize_name,
)
from apps.companies.models import Company, Source
from apps.companies.sources.base import RawCompany


@pytest.mark.parametrize(
    ("url", "domain"),
    [
        ("https://www.Foo.com/about?x=1", "foo.com"),
        ("http://foo.com", "foo.com"),
        ("foo.com/path", "foo.com"),
        ("www.foo.cat", "foo.cat"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_domain(url, domain):
    assert normalize_domain(url) == domain


def test_normalize_name_quita_acentos_sufijos_y_ruido():
    assert normalize_name("Agència Créativa, S.L.") == "agencia creativa"
    assert normalize_name("BUZZ BCN SLU") == "buzz"
    assert normalize_name("La Fábrica de Carbón S.L.") == "la fabrica de carbon"


def test_similitud_y_distancia():
    assert name_similarity("Buzz Agencia", "Agencia Buzz S.L.") >= 90
    assert name_similarity("Buzz", "Zoom PR") < 50
    assert round(distance_m(41.4036, 2.1744, 41.4036, 2.1744)) == 0
    assert 90 < distance_m(41.4036, 2.1744, 41.4045, 2.1744) < 110  # ~0.0009° lat ≈ 100 m


def _raw(**kw):
    base = {
        "source": Source.OSM,
        "external_id": "node/1",
        "name": "Buzz Agencia",
        "lat": 41.4036,
        "lng": 2.1744,
        "website": "https://www.buzzmn.com/",
    }
    base.update(kw)
    return RawCompany(**base)


@pytest.mark.django_db
def test_find_match_por_dominio_luego_por_nombre_y_distancia():
    buzz = Company.objects.create(name="Buzz", domain="buzzmn.com", lat=41.4036, lng=2.1744)
    Company.objects.create(name="Buzz Madrid", domain="buzzmn.com", lat=40.4, lng=-3.7)
    near = Company.objects.create(name="Estudi Cactus", lat=41.39, lng=2.16)

    assert find_match(_raw()) == (buzz, "domain")  # el más cercano de los que comparten dominio
    assert find_match(_raw(website="", name="Estudio Cactus S.L.", lat=41.3905, lng=2.16)) == (
        near,
        "name+distance",
    )
    assert find_match(_raw(website="", name="Estudio Cactus", lat=41.40, lng=2.16)) == (
        None,
        "",
    )  # a 1 km
    assert find_match(_raw(website="", name="Otro nombre", lat=41.39, lng=2.16)) == (None, "")
    assert find_match(_raw(website="", lat=None, lng=None)) == (None, "")


@pytest.mark.django_db
def test_merge_respeta_prioridad_y_guarda_procedencia():
    company = Company(name="Buzz")
    changed = merge_into(company, _raw(phone="930"), category=None)

    assert set(changed) >= {"website", "domain", "phone", "lat", "lng"}
    assert company.domain == "buzzmn.com"
    assert company.field_sources["phone"] == "osm"
    assert company.field_sources["name"] == "osm"

    # Una fuente menos prioritaria no pisa; la misma fuente sí actualiza su dato.
    merge_into(
        company,
        _raw(source=Source.PAGINAS_AMARILLAS, external_id="pa/1", phone="000", name="BUZZ SL"),
    )
    assert company.phone == "930"
    assert company.name == "Buzz Agencia"
    merge_into(company, _raw(phone="931"))
    assert company.phone == "931"

    # Una fuente más prioritaria sí pisa y se registra.
    merge_into(
        company,
        _raw(
            source=Source.FOURSQUARE,
            external_id="fsq/1",
            phone="+34 930 000 000",
            name="Buzz Agency",
        ),
    )
    assert company.phone == "+34 930 000 000"
    assert company.name == "Buzz Agency"
    assert company.field_sources["phone"] == "foursquare"


@pytest.mark.django_db
def test_confidence_score():
    empty = Company(name="x")
    assert confidence_score(empty, 1) == 35

    full = Company(
        name="x",
        website="https://x.com",
        phone="1",
        address="c/ x",
        lat=1.0,
        lng=1.0,
        category=Category.objects.get(slug="publicidad"),
    )
    assert confidence_score(full, 1) == 70
    assert confidence_score(full, 2) == 90
    assert confidence_score(full, 5) == 100
