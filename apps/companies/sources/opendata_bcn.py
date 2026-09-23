"""Fuente Open Data BCN: censo municipal de locales en planta baixa (CC-BY 4.0).

El Ajuntament publica cada pocos años el censo de locales a pie de calle con
nombre, actividad, dirección y coordenadas: ideal para entregar un CV en mano.
Las actividades son gruesas ("Serveis a les empreses i oficines"), así que la
categoría se deduce de palabras clave en el nombre del local, solo dentro de
las actividades donde viven agencias y estudios.

Se toma la edición más reciente vía la API CKAN del portal. El CSV (~25 MB)
no se cachea en la BD: se lee en streaming y solo se guardan las coincidencias.
Sustituye a los directorios sectoriales (Sortlist, Clutch, Páginas Amarillas),
que responden con desafíos anti-bot (ADR 0009).
"""

import csv
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import timedelta

import httpx

from ..http import USER_AGENT, FetchError, fetch
from ..models import Source
from .base import RawCompany, SourceError

logger = logging.getLogger(__name__)

PACKAGE_API = (
    "https://opendata-ajuntament.barcelona.cat/data/api/3/action/package_show"
    "?id=cens-locals-planta-baixa-act-economica"
)
# Actividades del censo (Codi_Activitat_2022) donde hay agencias, estudios y productoras.
ACTIVITY_CODES = {
    "1600400",  # Serveis a les empreses i oficines
    "1700700",  # Arts gràfiques
}
# (patrón sobre el nombre del local, slug). Gana la primera. Los nombres vienen en
# catalán, castellano o inglés y casi siempre en mayúsculas.
NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cowork", re.I), "coworkings"),
    (re.compile(r"publici|advertis", re.I), "publicidad"),
    (re.compile(r"comunica|\bpress\b|\brrpp\b|relacions p|relaciones p", re.I), "comunicacion"),
    (re.compile(r"marketing|m[aà]rqueting", re.I), "marketing-digital"),
    (re.compile(r"\bevent", re.I), "eventos"),
    (re.compile(r"produc(ci[oó]|tora)|\bfilms?\b|audiovisu|v[ií]deo", re.I), "productoras"),
    (re.compile(r"disse?ny|dise[nñ]o|design|creativ|branding", re.I), "diseno"),
]


def category_for_name(name: str) -> str | None:
    for pattern, slug in NAME_RULES:
        if pattern.search(name):
            return slug
    return None


def latest_csv_url() -> str:
    """URL del CSV de la edición más reciente (los recursos empiezan por el año)."""
    try:
        response = fetch(PACKAGE_API, ttl=timedelta(days=1), timeout=30)
    except FetchError as exc:
        raise SourceError(f"Open Data BCN no responde: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(f"Open Data BCN respondió {response.status_code}.")
    try:
        resources = response.json()["result"]["resources"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("Open Data BCN: respuesta CKAN inesperada.") from exc
    csvs = [
        r
        for r in resources
        if (r.get("format") or "").upper() == "CSV" and (r.get("name") or "")[:4].isdigit()
    ]
    if not csvs:
        raise SourceError("Open Data BCN: el censo no tiene ningún CSV.")
    return max(csvs, key=lambda r: r["name"])["url"]


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


def _stream_rows(url: str) -> Iterator[dict]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/csv"}
    try:
        with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as r:
            if r.status_code != 200:
                raise SourceError(f"Open Data BCN respondió {r.status_code} al descargar el censo.")
            r.encoding = "utf-8-sig"  # el CSV lleva BOM
            yield from csv.DictReader(r.iter_lines())
    except httpx.HTTPError as exc:
        raise SourceError(f"Descarga del censo fallida: {exc}") from exc


class OpenDataBcnAdapter:
    name = Source.OPENDATA_BCN

    def __init__(self, rows: Iterable[dict] | None = None):
        # `rows` permite inyectar filas en los tests sin tocar la red.
        self.rows = rows

    def fetch(self) -> Iterable[RawCompany]:
        rows = self.rows if self.rows is not None else _stream_rows(latest_csv_url())
        total = kept = 0
        for row in rows:
            total += 1
            raw = row_to_raw(row)
            if raw is not None:
                kept += 1
                yield raw
        logger.info("Open Data BCN: %d locales leídos, %d del sector", total, kept)
