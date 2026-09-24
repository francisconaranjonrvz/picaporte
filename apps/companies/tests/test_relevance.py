import pytest

from apps.catalog.models import Category
from apps.companies import services
from apps.companies.models import Company, Source
from apps.companies.relevance import (
    CORE_SECTORS,
    exclusion_reason,
    in_barcelona,
    junk_reason,
    searched_sectors,
)
from apps.profiles.models import Profile

from .test_services import FakeAdapter, _raw


@pytest.mark.parametrize(
    ("lat", "lng", "inside"),
    [
        (41.3870, 2.1701, True),  # plaça de Catalunya
        (41.4036, 2.1744, True),  # Sagrada Família
        (41.3995, 2.1210, True),  # Sarrià
        (41.4030, 2.2000, True),  # Poblenou
        (41.3596, 2.0997, False),  # L'Hospitalet
        (41.3553, 2.0700, False),  # Cornellà
        (41.3760, 2.0880, False),  # Esplugues
        (41.3260, 2.0950, False),  # El Prat
        (41.4500, 2.2470, False),  # Badalona
        (40.4168, -3.7038, False),  # Madrid
    ],
)
def test_termino_municipal(lat, lng, inside):
    assert in_barcelona(lat, lng) is inside


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Ecodisseny Imprempta Copisteria Retolacio", "imprenta o rótulos"),
        ("A.C. Retols Disseny", "imprenta o rótulos"),
        ("Gràfiques Món", "imprenta o rótulos"),
        ("Jordan Bcn Produccio Grafica", "imprenta o rótulos"),
        ("Publimprés Suministros Publicitarios", "imprenta o rótulos"),
        ("Meters Comunicacions I Software", "otra actividad"),
        ("Disseny D'Interiors & Arquitectura", "otra actividad"),
        ("Miray Telecomunicacions", "otra actividad"),
        ("WeWork", "coworking o centro de negocios"),
        ("Spaces Diagonal", "coworking o centro de negocios"),
        ("Santander Work Café", "coworking o centro de negocios"),
        ("7dos Co-working", "coworking o centro de negocios"),
        ("B188 Business Center", "coworking o centro de negocios"),
        ("Events", "nombre genérico"),
        ("Ogilvy", None),
        ("Bambu PR", None),
        ("Estudi de Disseny Francesc Moret", None),
        ("Sinergia Events", None),
    ],
)
def test_nombres_que_delatan_otra_actividad(name, reason):
    assert junk_reason(name) == reason


def test_motivo_de_exclusion():
    sectors = CORE_SECTORS
    assert exclusion_reason(_raw(), sectors) is None
    assert exclusion_reason(_raw(category_slug="medios"), sectors) == "sector no buscado"
    assert exclusion_reason(_raw(category_slug=None), sectors) == "sector no buscado"
    assert exclusion_reason(_raw(lat=41.3596, lng=2.0997), sectors) == "fuera de Barcelona"
    assert exclusion_reason(_raw(lat=None, lng=None), sectors) is None  # sin coordenadas: entra
    assert exclusion_reason(_raw(name="Gràfiques Món"), sectors) == "imprenta o rótulos"


@pytest.mark.django_db
def test_los_sectores_opcionales_salen_del_perfil(user):
    assert searched_sectors(None) == CORE_SECTORS
    profile = Profile.objects.create(user=user)
    assert searched_sectors(profile) == CORE_SECTORS
    # Una categoría retirada o básica marcada en el perfil no añade nada raro.
    profile.categories.set(Category.objects.filter(slug__in=["medios", "coworkings", "eventos"]))
    assert searched_sectors(profile) == CORE_SECTORS | {"medios"}


@pytest.mark.django_db
def test_run_discovery_descarta_y_retira_la_basura(monkeypatch):
    ctx = {"categories": {c.slug: c for c in Category.objects.all()}, "zones": []}
    viejas = [
        _raw(external_id="node/1", name="Gràfiques Món", website=""),
        _raw(external_id="node/2", name="Uikú", lat=41.3260, lng=2.0950, website=""),
        _raw(external_id="node/3", name="Ràdio Nova", category_slug="medios", website=""),
        _raw(external_id="node/4", name="Buzz", website="https://buzz.example"),
    ]
    for raw in viejas:
        services.ingest(raw, **ctx)
    assert Company.objects.filter(is_active=True).count() == 4

    adapter = FakeAdapter(viejas, name=Source.OSM)
    monkeypatch.setattr(services, "get_adapters", lambda names: [adapter])
    stats = services.run_discovery(["osm"], sectors=CORE_SECTORS)["osm"]

    assert stats.excluded == {
        "imprenta o rótulos": 1,
        "fuera de Barcelona": 1,
        "sector no buscado": 1,
    }
    assert stats.retired == 3
    assert list(Company.objects.filter(is_active=True).values_list("name", flat=True)) == ["Buzz"]

    # Si el perfil marca "medios", la radio vuelve en la siguiente búsqueda.
    services.run_discovery(["osm"], sectors=CORE_SECTORS | {"medios"})
    assert Company.objects.get(name="Ràdio Nova").is_active
