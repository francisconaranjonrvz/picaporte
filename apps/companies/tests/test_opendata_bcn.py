import json

import httpx
import pytest

from apps.companies.http import Fetched
from apps.companies.sources import opendata_bcn
from apps.companies.sources.base import SourceError
from apps.companies.sources.opendata_bcn import (
    OpenDataBcnAdapter,
    category_for_name,
    latest_csv_url,
    row_to_raw,
)

ROW = {
    "ID_Global": "5f15-abc",
    "Codi_Activitat_2022": "1600400",
    "Nom_Activitat": "Serveis a les empreses i oficines",
    "Nom_Local": "CHILLI  DESIGN",
    "Latitud": "41.402",
    "Longitud": "2.151",
    "Nom_Via": "BRUSI",
    "Num_Policia_Inicial": "36",
    "Num_Policia_Final": "38",
    "Nom_Barri": "Sant Gervasi - Galvany",
    "Nom_Districte": "Sarrià-Sant Gervasi",
    "Data_Revisio": "2023-05-16",
}


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("THE WOOD COWORKING", "coworkings"),
        ("ROAD PUBLICIDAD", "publicidad"),
        ("MAPA COMUNICACIÓ", "comunicacion"),
        ("NOBEL MARKETING", "marketing-digital"),
        ("SINERGIA EVENTS", "eventos"),
        ("BORDER FILMS", "productoras"),
        ("BLANC PRODUCCIONS", "productoras"),
        ("ESTUDI DE DISSENY FRANCESC MORET", "diseno"),
        ("TALKING DESIGN STUDIO", "diseno"),
        # Falsos positivos que las reglas evitan:
        ("ANTO IMPRESSORS", None),  # "press" dentro de impressors
        ("SEGURIDAD PREVENTIVA SP4", None),  # "event" dentro de preventiva
        ("FUJIFILM", None),
        ("GESTORIA PUIG", None),
    ],
)
def test_categoria_por_nombre(name, slug):
    assert category_for_name(name) == slug


def test_fila_a_raw():
    raw = row_to_raw(ROW)
    assert raw.source == "opendata_bcn"
    assert raw.external_id == "5f15-abc"
    assert raw.name == "Chilli Design"
    assert raw.category_slug == "diseno"
    assert raw.address == "Brusi, 36-38"
    assert (raw.lat, raw.lng) == (41.402, 2.151)
    assert raw.payload["barri"] == "Sant Gervasi - Galvany"


@pytest.mark.parametrize(
    "changes",
    [
        {"Codi_Activitat_2022": "1400002"},  # restaurante
        {"Nom_Local": "SN"},  # sense nom
        {"Nom_Local": "GESTORIA PUIG"},  # sin palabra clave del sector
    ],
)
def test_filas_que_no_interesan(changes):
    assert row_to_raw({**ROW, **changes}) is None


def test_numero_unico_coordenadas_invalidas_y_nombre_en_minusculas():
    raw = row_to_raw(
        {**ROW, "Num_Policia_Final": "", "Latitud": "n/d", "Nom_Local": "The Wood Coworking"}
    )
    assert raw.address == "Brusi, 36"
    assert raw.lat is None
    assert raw.name == "The Wood Coworking"  # solo se capitaliza lo que viene en mayúsculas


def test_adaptador_con_filas_inyectadas():
    rows = [ROW, {**ROW, "ID_Global": "x", "Codi_Activitat_2022": "1400002"}]
    assert [r.external_id for r in OpenDataBcnAdapter(rows=rows).fetch()] == ["5f15-abc"]


def _ckan(resources):
    return Fetched(200, json.dumps({"result": {"resources": resources}}), False)


def test_ultima_edicion_del_censo(monkeypatch):
    resources = [
        {"format": "CSV", "name": "2022_CensComercialBCN.csv", "url": "u2022"},
        {"format": "CSV", "name": "2024_CensComercial_BCN.csv", "url": "u2024"},
        {"format": "GeoJSON", "name": "2025_algo.geojson", "url": "geo"},
        {"format": "CSV", "name": "Llegenda.csv", "url": "ley"},
    ]
    monkeypatch.setattr(opendata_bcn, "fetch", lambda *a, **k: _ckan(resources))
    assert latest_csv_url() == "u2024"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Fetched(500, "", False), "500"),
        (Fetched(200, "no json", False), "CKAN"),
        (_ckan([{"format": "XLSX", "name": "2024.xlsx", "url": "x"}]), "CSV"),
    ],
)
def test_errores_de_ckan(monkeypatch, response, message):
    monkeypatch.setattr(opendata_bcn, "fetch", lambda *a, **k: response)
    with pytest.raises(SourceError, match=message):
        latest_csv_url()


def test_descarga_en_streaming_con_bom(monkeypatch):
    body = "﻿ID_Global,Codi_Activitat_2022,Nom_Local,Nom_Via\r\nid1,1600400,ROAD PUBLICIDAD,X\r\n"

    def handler(request):
        assert request.headers["User-Agent"].startswith("picaporte/")
        return httpx.Response(200, content=body.encode("utf-8"))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        opendata_bcn.httpx,
        "stream",
        lambda method, url, **kw: httpx.Client(transport=transport).stream(
            method, url, headers=kw.get("headers")
        ),
    )
    rows = list(opendata_bcn._stream_rows("https://example.org/censo.csv"))
    assert rows == [
        {
            "ID_Global": "id1",
            "Codi_Activitat_2022": "1600400",
            "Nom_Local": "ROAD PUBLICIDAD",
            "Nom_Via": "X",
        }
    ]


def test_descarga_con_error_http(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    monkeypatch.setattr(
        opendata_bcn.httpx,
        "stream",
        lambda method, url, **kw: httpx.Client(transport=transport).stream(method, url),
    )
    with pytest.raises(SourceError, match="404"):
        list(opendata_bcn._stream_rows("https://example.org/censo.csv"))
