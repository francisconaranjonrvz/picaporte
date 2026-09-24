"""Fuente Open Data BCN: censo municipal de locales en planta baixa (CC-BY 4.0).

El Ajuntament publica cada pocos años el censo de locales a pie de calle con
nombre, actividad, dirección y coordenadas: ideal para entregar un CV en mano.
Las actividades son gruesas ("Serveis a les empreses i oficines"), así que la
categoría se deduce de palabras clave en el nombre del local, solo dentro de
las actividades donde viven agencias y estudios.

Se toma la edición más reciente vía la API CKAN del portal y se consulta su
DataStore (`datastore_search`) filtrando en el servidor por actividad y pidiendo
solo las columnas útiles: ~2.500 filas en 3 páginas, cacheadas en la BD.
(La descarga directa del CSV redirige a un desafío anti-bot desde las IPs de
GitHub Actions; la API no.)
Sustituye a los directorios sectoriales (Sortlist, Clutch, Páginas Amarillas),
que responden con desafíos anti-bot (ADR 0009).
"""

import json
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import timedelta
from urllib.parse import urlencode

from ..http import FetchError, fetch
from ..models import Source
from .base import RawCompany, SourceError

logger = logging.getLogger(__name__)

API = "https://opendata-ajuntament.barcelona.cat/data/api/3/action"
PACKAGE_API = f"{API}/package_show?id=cens-locals-planta-baixa-act-economica"
PAGE_SIZE = 1000
FIELDS = [
    "ID_Global",
    "Codi_Activitat_2022",
    "Nom_Activitat",
    "Nom_Local",
    "Latitud",
    "Longitud",
    "Nom_Via",
    "Num_Policia_Inicial",
    "Num_Policia_Final",
    "Nom_Barri",
    "Nom_Districte",
    "Data_Revisio",
]
# Actividades del censo (Codi_Activitat_2022) donde hay agencias, estudios y productoras.
# "Arts gràfiques" (1700700) se dejó de pedir: son imprentas, copisterías y rotulistas.
ACTIVITY_CODES = {
    "1600400",  # Serveis a les empreses i oficines
}
# (patrón sobre el nombre del local, slug). Gana la primera. Los nombres vienen en
# catalán, castellano o inglés y casi siempre en mayúsculas.
NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"publici|advertis", re.I), "publicidad"),
    # Solo singular: "comunicacions/comunicaciones" suele ser una teleco.
    (
        re.compile(r"comunicaci[oó]n?\b|\brrpp\b|relacions p|relaciones p|public relations", re.I),
        "comunicacion",
    ),
    (re.compile(r"marketing|m[aà]rqueting", re.I), "marketing-digital"),
    (re.compile(r"\bevent", re.I), "eventos"),
    (
        re.compile(
            r"produc(ci[oó]|tora)|\bfilms?\b|audiovisu|v[ií]deo",
            re.I,
        ),
        "productoras",
    ),
    (re.compile(r"disse?ny|dise[nñ]o|design|creativ|branding", re.I), "diseno"),
    # Sectores opcionales (solo se guardan si el perfil los elige; ver relevance.py).
    (re.compile(r"editorial|edicions|ediciones", re.I), "editoriales"),
    (re.compile(r"fotograf|photo", re.I), "fotografia"),
    (
        re.compile(r"estudi de gravaci|estudio de grabaci|\brecords\b|\bm[uú]sic(a|s)?\b", re.I),
        "musica",
    ),
]


def category_for_name(name: str) -> str | None:
    for pattern, slug in NAME_RULES:
        if pattern.search(name):
            return slug
    return None


def _api(url: str) -> dict:
    """GET a la API CKAN (con caché) y devuelve `result`; cualquier anomalía es SourceError."""
    try:
        response = fetch(url, ttl=timedelta(days=7), timeout=60)
    except FetchError as exc:
        raise SourceError(f"Open Data BCN no responde: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(f"Open Data BCN respondió {response.status_code}.")
    try:
        body = response.json()
        if not body.get("success"):
            raise KeyError("success")
        return body["result"]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SourceError("Open Data BCN: respuesta CKAN inesperada.") from exc


def latest_resource_id() -> str:
    """Recurso de la edición más reciente cargado en el DataStore (los nombres empiezan por el año)."""
    resources = [
        r
        for r in _api(PACKAGE_API).get("resources", [])
        if r.get("datastore_active") and (r.get("name") or "")[:4].isdigit()
    ]
    if not resources:
        raise SourceError("Open Data BCN: el censo no tiene ninguna edición en el DataStore.")
    return max(resources, key=lambda r: r["name"])["id"]


def search_url(resource_id: str, offset: int) -> str:
    params = {
        "resource_id": resource_id,
        "fields": ",".join(FIELDS),
        "filters": json.dumps({"Codi_Activitat_2022": sorted(ACTIVITY_CODES)}),
        "sort": "ID_Global",  # orden estable entre páginas
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    return f"{API}/datastore_search?{urlencode(params)}"


def datastore_rows(resource_id: str) -> Iterator[dict]:
    offset = 0
    while True:
        result = _api(search_url(resource_id, offset))
        records = result.get("records") or []
        yield from records
        offset += len(records)
        if not records or offset >= int(result.get("total") or 0):
            return


def _address(row: dict) -> str:
    street = (row.get("Nom_Via") or "").strip().title()
    first = (row.get("Num_Policia_Inicial") or "").strip()
    last = (row.get("Num_Policia_Final") or "").strip()
    number = f"{first}-{last}" if first and last and last != first else first
    return f"{street}, {number}" if street and number else street


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def row_to_raw(row: dict) -> RawCompany | None:
    if row.get("Codi_Activitat_2022") not in ACTIVITY_CODES:
        return None
    name = " ".join((row.get("Nom_Local") or "").split())
    if not name or name.upper() == "SN":  # "sense nom"
        return None
    slug = category_for_name(name)
    if slug is None:
        return None
    if name.isupper():  # el censo grita: "CHILLI DESIGN" -> "Chilli Design"
        name = name.title()
    return RawCompany(
        source=Source.OPENDATA_BCN,
        external_id=row["ID_Global"],
        name=name,
        category_slug=slug,
        lat=_float(row.get("Latitud")),
        lng=_float(row.get("Longitud")),
        address=_address(row),
        city="Barcelona",
        payload={
            "activitat": row.get("Nom_Activitat"),
            "barri": row.get("Nom_Barri"),
            "districte": row.get("Nom_Districte"),
            "data_revisio": row.get("Data_Revisio"),
        },
    )


class OpenDataBcnAdapter:
    name = Source.OPENDATA_BCN

    def __init__(self, rows: Iterable[dict] | None = None):
        # `rows` permite inyectar filas en los tests sin tocar la red.
        self.rows = rows

    def fetch(self) -> Iterable[RawCompany]:
        rows = self.rows if self.rows is not None else datastore_rows(latest_resource_id())
        total = kept = 0
        for row in rows:
            total += 1
            raw = row_to_raw(row)
            if raw is not None:
                kept += 1
                yield raw
        logger.info("Open Data BCN: %d locales leídos, %d del sector", total, kept)
