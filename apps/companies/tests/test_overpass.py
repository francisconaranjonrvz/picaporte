import json

import pytest

from apps.companies.http import Fetched, FetchError
from apps.companies.sources import overpass
from apps.companies.sources.base import SourceError
from apps.companies.sources.overpass import QUERY, OverpassAdapter, category_for, element_to_raw

NODE = {
    "type": "node",
    "id": 123,
    "lat": 41.4036,
    "lon": 2.1744,
    "tags": {
        "name": "Buzz Agencia",
        "office": "advertising_agency",
        "addr:street": "Carrer de Pujades",
        "addr:housenumber": "51",
        "addr:postcode": "08005",
        "contact:website": "https://www.buzzmn.com/",
        "contact:phone": "+34 930 000 000",
        "opening_hours": "Mo-Fr 09:00-18:00",
    },
}
WAY = {
    "type": "way",
    "id": 456,
    "center": {"lat": 41.39, "lon": 2.16},
    "tags": {"name": "Cowork Gràcia", "amenity": "coworking_space", "website": "cowork.cat"},
}
STUDIO = {
    "type": "node",
    "id": 7,
    "lat": 41.4,
    "lon": 2.2,
    "tags": {"name": "Prod", "amenity": "studio", "studio": "video"},
}
UNNAMED = {"type": "node", "id": 8, "lat": 41.4, "lon": 2.2, "tags": {"office": "marketing"}}


def test_la_consulta_usa_filtros_exactos_bbox_y_centros():
    assert QUERY.startswith("[out:json][timeout:90][bbox:41.32,2.05,41.47,2.23];")
    assert 'nwr["office"="advertising_agency"];' in QUERY
    assert 'nwr["amenity"="studio"]["studio"="video"];' in QUERY
    assert "~" not in QUERY  # sin regex: los índices de Overpass no los usan
    assert QUERY.endswith("out center;")


@pytest.mark.parametrize(
    ("tags", "slug"),
    [
        ({"office": "advertising_agency"}, "publicidad"),
        ({"office": "marketing"}, "marketing-digital"),
        ({"office": "graphic_design"}, "diseno"),
        ({"craft": "web_design"}, "diseno"),
        ({"office": "event_management"}, "eventos"),
        ({"office": "public_relations"}, "comunicacion"),
        ({"office": "coworking"}, "coworkings"),
        ({"amenity": "coworking_space"}, "coworkings"),
        ({"amenity": "studio", "studio": "television"}, "productoras"),
        ({"amenity": "studio", "studio": "radio"}, None),
        ({"office": "telecommunication"}, None),
    ],
)
def test_mapeo_de_etiquetas_a_categorias(tags, slug):
    assert category_for(tags) == slug


def test_nodo_a_rawcompany():
    raw = element_to_raw(NODE)

    assert raw.external_id == "node/123"
    assert raw.name == "Buzz Agencia"
    assert raw.category_slug == "publicidad"
    assert (raw.lat, raw.lng) == (41.4036, 2.1744)
    assert raw.address == "Carrer de Pujades, 51"
    assert raw.postcode == "08005"
    assert raw.city == "Barcelona"
    assert raw.website == "https://www.buzzmn.com/"
    assert raw.phone == "+34 930 000 000"
    assert raw.opening_hours == "Mo-Fr 09:00-18:00"
    assert raw.payload["tags"]["office"] == "advertising_agency"


def test_way_usa_el_centro_y_sin_nombre_se_descarta():
    raw = element_to_raw(WAY)

    assert raw.external_id == "way/456"
    assert (raw.lat, raw.lng) == (41.39, 2.16)
    assert raw.website == "cowork.cat"
    assert element_to_raw(UNNAMED) is None
    assert element_to_raw(STUDIO).category_slug == "productoras"


def _body(*elements):
    return json.dumps({"elements": list(elements)})


def test_fetch_devuelve_rawcompanies(monkeypatch):
    monkeypatch.setattr(
        overpass, "fetch", lambda url, **kw: Fetched(200, _body(NODE, WAY, UNNAMED), False)
    )

    raws = list(OverpassAdapter().fetch())

    assert [r.name for r in raws] == ["Buzz Agencia", "Cowork Gràcia"]


def test_reintenta_tras_504_y_luego_usa_el_espejo(monkeypatch):
    calls = []
    outcomes = [Fetched(504, "", False), Fetched(504, "", False), Fetched(200, _body(NODE), False)]

    def fake_fetch(url, **kwargs):
        calls.append(url)
        return outcomes.pop(0)

    monkeypatch.setattr(overpass, "fetch", fake_fetch)
    monkeypatch.setattr(overpass.time, "sleep", lambda s: calls.append(f"sleep {s}"))

    raws = list(OverpassAdapter().fetch())

    assert len(raws) == 1
    assert calls == [
        overpass.OVERPASS_URL,
        "sleep 30",
        overpass.OVERPASS_URL,
        "sleep 30",
        overpass.FALLBACK_URLS[0],
    ]


def test_406_es_error_inmediato_y_agotar_espejos_tambien(monkeypatch):
    monkeypatch.setattr(overpass, "fetch", lambda url, **kw: Fetched(406, "", False))
    with pytest.raises(SourceError, match="User-Agent"):
        list(OverpassAdapter().fetch())

    def caido(url, **kwargs):
        raise FetchError("conexión rechazada")

    monkeypatch.setattr(overpass, "fetch", caido)
    with pytest.raises(SourceError, match="saturado o caído"):
        list(OverpassAdapter().fetch())


def test_respuesta_no_json_es_error(monkeypatch):
    monkeypatch.setattr(overpass, "fetch", lambda url, **kw: Fetched(200, "<html>", False))

    with pytest.raises(SourceError, match="JSON"):
        list(OverpassAdapter().fetch())
