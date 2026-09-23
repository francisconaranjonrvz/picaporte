import json
from datetime import date

import pytest

from apps.companies.http import Fetched
from apps.companies.sources import foursquare
from apps.companies.sources.base import SourceError
from apps.companies.sources.foursquare import (
    LABEL_REGEX,
    FoursquareAdapter,
    category_for_labels,
    is_stale,
    latest_release,
    places_glob,
    row_to_raw,
)

duckdb = pytest.importorskip("duckdb")

BPS = "Business and Professional Services"


@pytest.mark.parametrize(
    ("labels", "slug"),
    [
        ([f"{BPS} > Office > Coworking Space"], "coworkings"),
        ([f"{BPS} > Advertising Agency"], "publicidad"),
        ([f"{BPS} > Public Relations Firm"], "comunicacion"),
        ([f"{BPS} > Media Agency"], "comunicacion"),
        ([f"{BPS} > Marketing Agency"], "marketing-digital"),
        ([f"{BPS} > Event Service"], "eventos"),
        ([f"{BPS} > Film Studio"], "productoras"),
        ([f"{BPS} > Technology Business > Website Designer"], "diseno"),
        ([f"{BPS} > Design Studio"], "diseno"),
        # La hoja manda: una categoría hija de "Event" no es una agencia de eventos.
        (["Event > Entertainment Event"], None),
        # "Telecommunications" o un edificio universitario no son comunicación.
        ([f"{BPS} > Technology Business > Telecommunications Service"], None),
        (["Community and Government > Education > College Communications Building"], None),
        (["Dining and Drinking > Restaurant"], None),
        # Si la principal no encaja, se prueba la secundaria.
        (["Retail > Print Store", f"{BPS} > Advertising Agency"], "publicidad"),
        ([], None),
        (None, None),
    ],
)
def test_categoria_por_etiquetas(labels, slug):
    assert category_for_labels(labels) == slug


def test_el_prefiltro_sql_cubre_todas_las_reglas():
    for pattern, _ in foursquare.CATEGORY_RULES:
        assert pattern.pattern in LABEL_REGEX
    assert LABEL_REGEX.startswith("(?i)(")


ROW = {
    "fsq_place_id": "abc123",
    "name": " Agencia Sol ",
    "latitude": 41.39,
    "longitude": 2.17,
    "address": "Carrer de Pujades 51",
    "locality": "Barcelona",
    "postcode": "08005",
    "tel": "+34 930 00 00 00",
    "website": "agenciasol.com",
    "email": "hola@agenciasol.com",
    "fsq_category_labels": [f"{BPS} > Advertising Agency"],
    "date_refreshed": "2026-08-01",
}


def test_fila_a_raw_normaliza_web_y_guarda_etiquetas():
    raw = row_to_raw(ROW)
    assert raw.source == "foursquare"
    assert raw.external_id == "abc123"
    assert raw.name == "Agencia Sol"
    assert raw.category_slug == "publicidad"
    assert raw.website == "https://agenciasol.com"
    assert raw.phone == "+34 930 00 00 00"
    assert raw.payload["labels"] == [f"{BPS} > Advertising Agency"]


def test_fila_sin_nombre_o_sin_categoria_se_descarta():
    assert row_to_raw({**ROW, "name": "  "}) is None
    assert row_to_raw({**ROW, "fsq_category_labels": ["Retail > Print Store"]}) is None


def test_web_con_esquema_se_respeta_y_localidad_vacia_es_barcelona():
    raw = row_to_raw({**ROW, "website": "http://x.es", "locality": None})
    assert raw.website == "http://x.es"
    assert raw.city == "Barcelona"


def test_lugar_sin_refrescar_en_tres_anos_es_obsoleto():
    today = date(2026, 9, 23)
    assert not is_stale({"date_refreshed": date(2025, 1, 1)}, today)
    assert is_stale({"date_refreshed": date(2023, 1, 1)}, today)
    assert is_stale({"date_refreshed": "2020-05-01"}, today)
    assert not is_stale({"date_refreshed": None}, today)


def _write_parquet(path, rows):
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE places (
            fsq_place_id VARCHAR, name VARCHAR, latitude DOUBLE, longitude DOUBLE,
            address VARCHAR, locality VARCHAR, postcode VARCHAR, tel VARCHAR,
            website VARCHAR, email VARCHAR, fsq_category_labels VARCHAR[],
            date_refreshed DATE, date_closed DATE)"""
    )
    for r in rows:
        con.execute(
            "INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                r["fsq_place_id"],
                r["name"],
                r["latitude"],
                r["longitude"],
                r.get("address"),
                r.get("locality"),
                r.get("postcode"),
                r.get("tel"),
                r.get("website"),
                r.get("email"),
                r["fsq_category_labels"],
                r.get("date_refreshed"),
                r.get("date_closed"),
            ],
        )
    con.execute(f"COPY places TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def test_adaptador_filtra_bbox_cerradas_y_categorias_sobre_parquet(tmp_path):
    path = tmp_path / "places.parquet"
    _write_parquet(
        path,
        [
            ROW,
            {**ROW, "fsq_place_id": "madrid", "latitude": 40.4, "longitude": -3.7},
            {**ROW, "fsq_place_id": "cerrada", "date_closed": "2025-01-01"},
            {**ROW, "fsq_place_id": "vieja", "date_refreshed": "2019-01-01"},
            {**ROW, "fsq_place_id": "bar", "fsq_category_labels": ["Dining and Drinking > Bar"]},
            {
                **ROW,
                "fsq_place_id": "cowork",
                "name": "Cowork Poblenou",
                "fsq_category_labels": [f"{BPS} > Office > Coworking Space"],
            },
        ],
    )
    raws = list(FoursquareAdapter(path=str(path)).fetch())
    assert sorted(r.external_id for r in raws) == ["abc123", "cowork"]
    assert {r.category_slug for r in raws} == {"publicidad", "coworkings"}


def test_sin_token_falla_con_mensaje_claro(monkeypatch):
    monkeypatch.setattr(foursquare, "latest_release", lambda: "dt=2026-09-15")
    with pytest.raises(SourceError, match="HF_TOKEN"):
        list(FoursquareAdapter(token="").fetch())


def test_parquet_inexistente_es_source_error(tmp_path):
    with pytest.raises(SourceError, match="Foursquare"):
        list(FoursquareAdapter(path=str(tmp_path / "no.parquet")).fetch())


def test_ultima_version_y_ruta_hf(monkeypatch):
    listing = [
        {"type": "directory", "path": "release/dt=2026-08-12"},
        {"type": "directory", "path": "release/dt=2026-09-15"},
        {"type": "file", "path": "release/README.md"},
    ]
    monkeypatch.setattr(
        foursquare, "fetch", lambda *a, **k: Fetched(200, json.dumps(listing), False)
    )
    assert latest_release() == "dt=2026-09-15"
    assert places_glob("dt=2026-09-15") == (
        "hf://datasets/foursquare/fsq-os-places/release/dt=2026-09-15/places/parquet/*.parquet"
    )


def test_ultima_version_con_error_http(monkeypatch):
    monkeypatch.setattr(foursquare, "fetch", lambda *a, **k: Fetched(503, "", False))
    with pytest.raises(SourceError, match="503"):
        latest_release()
    monkeypatch.setattr(foursquare, "fetch", lambda *a, **k: Fetched(200, "[]", False))
    with pytest.raises(SourceError, match="versiones"):
        latest_release()
