"""Fuente Foursquare OS Places (Apache 2.0, gratis) vía el dataset de Hugging Face.

El dataset (`foursquare/fsq-os-places`) es un volcado mensual en parquet de
~100 M de lugares (~11 GB). No se descarga: DuckDB lo lee por HTTP con
`hf://` y solo baja las columnas y los row groups que pasan el filtro
(bbox de Barcelona, sin fecha de cierre, categoría del sector).
Es un dataset con acceso restringido: hace falta aceptar sus condiciones en
Hugging Face y un token de lectura en `HF_TOKEN` (secreto del entorno
`production` en GitHub; solo lo usa el worker, nunca Vercel).

Las categorías se reconocen por palabras clave en las etiquetas jerárquicas
(`Business and Professional Services > Event Service`), que viajan con cada
lugar; así no dependemos de ids opacos.
"""

import logging
import os
import re
from collections import Counter
from collections.abc import Iterable
from datetime import timedelta

from ..http import FetchError, fetch
from ..models import Source
from .base import RawCompany, SourceError

logger = logging.getLogger(__name__)

DATASET = "foursquare/fsq-os-places"
RELEASES_API = f"https://huggingface.co/api/datasets/{DATASET}/tree/main/release"
# Mismo bbox que Overpass: (sur, oeste, norte, este).
BBOX = (41.32, 2.05, 41.47, 2.23)

# (patrón sobre la hoja de la etiqueta, slug de categoría). Gana la primera regla que encaje.
CATEGORY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cowork", re.I), "coworkings"),
    (re.compile(r"advertis", re.I), "publicidad"),
    (re.compile(r"public relations|media agency|\bcommunications\b", re.I), "comunicacion"),
    (re.compile(r"marketing", re.I), "marketing-digital"),
    (re.compile(r"event (service|plann|management)", re.I), "eventos"),
    (
        re.compile(r"film studio|video production|television station|tv station", re.I),
        "productoras",
    ),
    (re.compile(r"graphic design|design studio|website designer|web design", re.I), "diseno"),
]
# Prefiltro en SQL: unión de las reglas (DuckDB usa RE2, sin flags inline salvo (?i)).
LABEL_REGEX = "(?i)(" + "|".join(p.pattern for p, _ in CATEGORY_RULES) + ")"


def category_for_labels(labels: Iterable[str] | None) -> str | None:
    """Primera categoría reconocida en las hojas de las etiquetas (la principal va primero)."""
    for label in labels or []:
        leaf = label.rsplit(">", 1)[-1].strip()
        for pattern, slug in CATEGORY_RULES:
            if pattern.search(leaf):
                return slug
    return None


def latest_release() -> str:
    """Carpeta `dt=AAAA-MM-DD` más reciente (el listado del repo es público aunque esté restringido)."""
    try:
        response = fetch(RELEASES_API, ttl=timedelta(days=1), timeout=30)
    except FetchError as exc:
        raise SourceError(f"No se pudo listar las versiones de {DATASET}: {exc}") from exc
    if response.status_code != 200:
        raise SourceError(f"Hugging Face respondió {response.status_code} al listar versiones.")
    releases = sorted(
        item["path"].rsplit("/", 1)[-1]
        for item in response.json()
        if item.get("type") == "directory" and "dt=" in item.get("path", "")
    )
    if not releases:
        raise SourceError(f"{DATASET} no tiene versiones publicadas.")
    return releases[-1]


def places_glob(release: str) -> str:
    return f"hf://datasets/{DATASET}/release/{release}/places/parquet/*.parquet"


QUERY = """
SELECT fsq_place_id, name, latitude, longitude, address, locality, postcode,
       tel, website, email, fsq_category_labels, date_refreshed
FROM read_parquet($path)
WHERE latitude BETWEEN $south AND $north
  AND longitude BETWEEN $west AND $east
  AND date_closed IS NULL
  AND name IS NOT NULL
  AND regexp_matches(array_to_string(fsq_category_labels, ' | '), $labels)
"""


def _website(value: str | None) -> str:
    value = (value or "").strip()
    if value and not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def row_to_raw(row: dict) -> RawCompany | None:
    name = (row.get("name") or "").strip()
    labels = list(row.get("fsq_category_labels") or [])
    slug = category_for_labels(labels)
    if not name or slug is None:
        return None
    refreshed = row.get("date_refreshed")
    return RawCompany(
        source=Source.FOURSQUARE,
        external_id=row["fsq_place_id"],
        name=name,
        category_slug=slug,
        lat=row.get("latitude"),
        lng=row.get("longitude"),
        address=(row.get("address") or "").strip(),
        postcode=(row.get("postcode") or "").strip(),
        city=(row.get("locality") or "").strip() or "Barcelona",
        website=_website(row.get("website")),
        phone=(row.get("tel") or "").strip(),
        email=(row.get("email") or "").strip(),
        payload={
            "fsq_place_id": row["fsq_place_id"],
            "labels": labels,
            "date_refreshed": str(refreshed) if refreshed else None,
        },
    )


class FoursquareAdapter:
    name = Source.FOURSQUARE

    def __init__(self, path: str | None = None, token: str | None = None):
        # `path` permite apuntar a un parquet local (tests) sin tocar la red.
        self.path = path
        self.token = token if token is not None else os.environ.get("HF_TOKEN", "")

    def _connect(self):
        try:
            import duckdb  # solo en el worker (grupo `worker` de uv); Vercel no lo instala
        except ImportError as exc:
            raise SourceError("Falta DuckDB: `uv sync --group worker`.") from exc
        con = duckdb.connect()
        if self.path is None:
            if not self.token:
                raise SourceError(
                    f"Falta HF_TOKEN (token de lectura de Hugging Face con acceso a {DATASET})."
                )
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute("CREATE SECRET hf (TYPE huggingface, TOKEN $token)", {"token": self.token})
        return con

    def _rows(self) -> list[dict]:
        path = self.path or places_glob(latest_release())
        south, west, north, east = BBOX
        con = self._connect()
        try:
            cursor = con.execute(
                QUERY,
                {
                    "path": path,
                    "south": south,
                    "north": north,
                    "west": west,
                    "east": east,
                    "labels": LABEL_REGEX,
                },
            )
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
        except Exception as exc:  # duckdb.Error y fallos HTTP llegan con tipos variados
            raise SourceError(f"Lectura de Foursquare OS Places fallida: {exc}") from exc
        finally:
            con.close()

    def fetch(self) -> Iterable[RawCompany]:
        rows = self._rows()
        leaves = Counter(
            (row.get("fsq_category_labels") or ["?"])[0].rsplit(">", 1)[-1].strip() for row in rows
        )
        logger.info("Foursquare: %d lugares; etiquetas: %s", len(rows), dict(leaves.most_common()))
        for row in rows:
            raw = row_to_raw(row)
            if raw is not None:
                yield raw
