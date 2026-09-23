"""Fuente Sortlist: directorio de agencias con listados públicos por servicio y ciudad.

Cada listado (`/publicidad/barcelona-es`) lleva en `__NEXT_DATA__` las 20
agencias de la página con nombre, web y ciudades con oficina. Sortlist no da
la dirección exacta: sus empresas entran sin coordenadas y la dirección se
busca después en su web (enriquecimiento, fase 4). Solo se guardan las que
declaran oficina en Barcelona.

Buenas prácticas: se consulta `robots.txt` antes de cada URL, se espera
`DELAY_SECONDS` entre peticiones que salen a la red, se limita el número de
páginas por listado y las respuestas se cachean 7 días.
Clutch y Páginas Amarillas se descartaron: responden con desafíos anti-bot
(Cloudflare, Incapsula) y saltárselos no es aceptable.
"""

import json
import logging
import re
import time
from collections.abc import Iterable
from datetime import timedelta

from ..http import FetchError, fetch, robots_allows
from ..models import Source
from .base import RawCompany, SourceError

logger = logging.getLogger(__name__)

BASE_URL = "https://www.sortlist.es"
# Listado de Sortlist -> slug de categoría de Picaporte.
LISTINGS = {
    "publicidad": "publicidad",
    "eventos": "eventos",
    "diseno-grafico": "diseno",
    "branding": "diseno",
    "rrpp": "comunicacion",
    "marketing-online": "marketing-digital",
    "video-promocional": "productoras",
}
CITY = "barcelona-es"
MAX_PAGES = 5  # 20 agencias por página, ordenadas por relevancia
DELAY_SECONDS = 3.0
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


def listing_url(listing: str, page: int) -> str:
    url = f"{BASE_URL}/{listing}/{CITY}"
    return url if page <= 1 else f"{url}?page={page}"


def parse_listing(html: str) -> tuple[list[dict], int]:
    """(agencias de la página, número de la última página). Formato inesperado -> SourceError."""
    match = NEXT_DATA.search(html)
    if not match:
        raise SourceError("Sortlist: la página no trae __NEXT_DATA__ (¿ha cambiado el formato?).")
    try:
        organic = json.loads(match.group(1))["props"]["pageProps"]["data"]["organicAgencies"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("Sortlist: estructura de __NEXT_DATA__ desconocida.") from exc
    agencies = [item for item in organic.get("included", []) if item.get("type") == "agency"]
    pagination = (organic.get("meta") or {}).get("pagination") or {}
    return agencies, int(pagination.get("last") or 0)


def _cities(attributes: dict) -> list[str]:
    cities = []
    for entry in attributes.get("addresses") or []:
        address = entry.get("address") or {}
        cities.append(address.get("es") or address.get("en") or "")
    return [c for c in cities if c]


def agency_to_raw(agency: dict, category_slug: str) -> RawCompany | None:
    attributes = agency.get("attributes") or {}
    name = (attributes.get("name") or "").strip()
    cities = _cities(attributes)
    if not name or not any("barcel" in c.lower() for c in cities):
        return None
    return RawCompany(
        source=Source.SORTLIST,
        external_id=str(agency["id"]),
        name=name,
        category_slug=category_slug,
        city="Barcelona",
        website=(attributes.get("website_url") or attributes.get("website") or "").strip(),
        payload={
            "slug": attributes.get("slug"),
            "tagline": attributes.get("tagline"),
            "team_size": attributes.get("team_size"),
            "reviews_count": attributes.get("reviews_count"),
            "cities": cities,
            "profile": f"{BASE_URL}/agency/{attributes.get('slug')}",
        },
    )


class SortlistAdapter:
    name = Source.SORTLIST

    def __init__(
        self,
        listings: dict[str, str] | None = None,
        max_pages: int = MAX_PAGES,
        delay: float = DELAY_SECONDS,
    ):
        self.listings = listings or LISTINGS
        self.max_pages = max_pages
        self.delay = delay

    def _get(self, url: str) -> str | None:
        if not robots_allows(url):
            logger.warning("robots.txt no permite %s; se omite", url)
            return None
        try:
            response = fetch(url, ttl=timedelta(days=7), timeout=60, accept="text/html")
        except FetchError as exc:
            raise SourceError(f"Sortlist no responde: {exc}") from exc
        if not response.cached:
            time.sleep(self.delay)
        if response.status_code == 404:
            logger.warning("Sortlist: %s no existe (404)", url)
            return None
        if response.status_code != 200:
            raise SourceError(f"Sortlist respondió {response.status_code} en {url}")
        return response.text

    def fetch(self) -> Iterable[RawCompany]:
        seen: set[str] = set()
        for listing, category_slug in self.listings.items():
            page, last = 1, 1
            while page <= min(last, self.max_pages):
                html = self._get(listing_url(listing, page))
                if html is None:
                    break
                agencies, last = parse_listing(html)
                logger.info("Sortlist %s p.%d: %d agencias", listing, page, len(agencies))
                for agency in agencies:
                    raw = agency_to_raw(agency, category_slug)
                    # Una agencia sale en varios listados: cuenta la primera (orden de LISTINGS).
                    if raw is not None and raw.external_id not in seen:
                        seen.add(raw.external_id)
                        yield raw
                page += 1
