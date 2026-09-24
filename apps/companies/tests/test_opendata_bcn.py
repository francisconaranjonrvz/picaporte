import json
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.companies.http import Fetched, FetchError
from apps.companies.sources import opendata_bcn
from apps.companies.sources.base import SourceError
from apps.companies.sources.opendata_bcn import (
    OpenDataBcnAdapter,
    category_for_name,
    datastore_rows,
    latest_resource_id,
    row_to_raw,
    search_url,
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
        ("ROAD PUBLICIDAD", "publicidad"),
        ("MAPA COMUNICACIÓ", "comunicacion"),
        ("NOBEL MARKETING", "marketing-digital"),
        ("SINERGIA EVENTS", "eventos"),
        ("BORDER FILMS", "productoras"),
        ("BLANC PRODUCCIONS", "productoras"),
        ("ESTUDI DE DISSENY FRANCESC MORET", "diseno"),
        ("TALKING DESIGN STUDIO", "diseno"),
        ("EDICIONS DEL PERISCOPI", "editoriales"),
        ("ESTUDI FOTOGRAFIC NOU", "fotografia"),
        # Falsos positivos que las reglas evitan:
        ("ANTO IMPRESSORS", None),  # "press" dentro de impressors
        ("SEGURIDAD PREVENTIVA SP4", None),  # "event" dentro de preventiva
        ("FUJIFILM", None),
        ("GESTORIA PUIG", None),
        ("THE WOOD COWORKING", None),  # los coworkings ya no se buscan
        ("DITEC COMUNICACIONES", None),  # plural: suele ser una teleco
        ("PRESS I CAR BCN", None),
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
        {"Codi_Activitat_2022": "1700700"},  # arts gràfiques: imprentas y rotulistas
        {"Nom_Local": "SN"},  # sense nom
        {"Nom_Local": "GESTORIA PUIG"},  # sin palabra clave del sector
    ],
)
def test_filas_que_no_interesan(changes):
    assert row_to_raw({**ROW, **changes}) is None


def test_numero_unico_coordenadas_invalidas_y_nombre_en_minusculas():
    raw = row_to_raw(
        {**ROW, "Num_Policia_Final": "", "Latitud": "n/d", "Nom_Local": "The Wood Design"}
    )
    assert raw.address == "Brusi, 36"
    assert raw.lat is None
    assert raw.name == "The Wood Design"  # solo se capitaliza lo que viene en mayúsculas


def test_adaptador_con_filas_inyectadas():
    rows = [ROW, {**ROW, "ID_Global": "x", "Codi_Activitat_2022": "1400002"}]
    assert [r.external_id for r in OpenDataBcnAdapter(rows=rows).fetch()] == ["5f15-abc"]


def _ok(result):
    return Fetched(200, json.dumps({"success": True, "result": result}), False)


def test_ultima_edicion_en_el_datastore(monkeypatch):
    resources = [
        {"name": "2022_CensComercialBCN.csv", "id": "r2022", "datastore_active": True},
        {"name": "2024_CensComercial_BCN.csv", "id": "r2024", "datastore_active": True},
        {"name": "2025_algo.geojson", "id": "geo", "datastore_active": False},
        {"name": "Llegenda.csv", "id": "ley", "datastore_active": True},
    ]
    monkeypatch.setattr(opendata_bcn, "fetch", lambda *a, **k: _ok({"resources": resources}))
    assert latest_resource_id() == "r2024"


def test_busqueda_filtra_en_servidor_y_pide_solo_columnas_utiles():
    query = parse_qs(urlsplit(search_url("r2024", 1000)).query)
    assert query["resource_id"] == ["r2024"]
    assert json.loads(query["filters"][0]) == {"Codi_Activitat_2022": ["1600400"]}
    assert query["fields"][0].split(",") == opendata_bcn.FIELDS
    assert (query["limit"], query["offset"]) == (["1000"], ["1000"])


def test_paginacion_del_datastore(monkeypatch):
    pages = {
        0: {"records": [{"ID_Global": "a"}, {"ID_Global": "b"}], "total": 3},
        2: {"records": [{"ID_Global": "c"}], "total": 3},
    }
    requested = []

    def fake_fetch(url, **kwargs):
        offset = int(parse_qs(urlsplit(url).query)["offset"][0])
        requested.append(offset)
        return _ok(pages[offset])

    monkeypatch.setattr(opendata_bcn, "fetch", fake_fetch)
    assert [r["ID_Global"] for r in datastore_rows("r2024")] == ["a", "b", "c"]
    assert requested == [0, 2]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Fetched(500, "", False), "500"),
        (Fetched(200, "<html>challenge</html>", False), "CKAN"),  # desafío anti-bot
        (Fetched(200, json.dumps({"success": False}), False), "CKAN"),
        (
            _ok({"resources": [{"name": "2024.csv", "id": "x", "datastore_active": False}]}),
            "DataStore",
        ),
    ],
)
def test_errores_de_ckan(monkeypatch, response, message):
    monkeypatch.setattr(opendata_bcn, "fetch", lambda *a, **k: response)
    with pytest.raises(SourceError, match=message):
        latest_resource_id()


def test_sin_red(monkeypatch):
    def fail(*args, **kwargs):
        raise FetchError("dns")

    monkeypatch.setattr(opendata_bcn, "fetch", fail)
    with pytest.raises(SourceError, match="no responde"):
        list(OpenDataBcnAdapter().fetch())
