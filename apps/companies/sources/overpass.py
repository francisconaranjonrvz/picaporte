"""Fuente OpenStreetMap vía Overpass API (datos ODbL, gratis).

Una sola consulta al bbox de Barcelona con todas las etiquetas relevantes
(filtros exactos, que usan índice); la respuesta se cachea 7 días. Si el
servidor principal está saturado (429/504) se reintenta tras 30 s y luego
se prueba un espejo.
Cobertura verificada en taginfo (sept. 2026): publicidad, diseño y marketing
están bien etiquetados; RRPP, eventos y productoras muy poco, así que OSM es
una fuente parcial que complementan las demás. Los coworkings ya no se piden
(no contratan). Qué se guarda lo decide `apps.companies.relevance`.
"""

import logging
import time
from collections.abc import Iterable
from datetime import timedelta

from ..http import Fetched, FetchError, fetch, forget
from ..models import Source
from .base import RawCompany, SourceError

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FALLBACK_URLS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
PRIMARY_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 30  # política de overpass-api.de tras un 429/504; crece con cada intento
# bbox del municipio de Barcelona (sur, oeste, norte, este); la zona se asigna luego por bbox fino.
BARCELONA_BBOX = "41.32,2.05,41.47,2.23"

OFFICE_TO_CATEGORY = {
    "advertising_agency": "publicidad",
    "advertising": "publicidad",
    "marketing": "marketing-digital",
    "marketing_agency": "marketing-digital",
    "digital_marketing": "marketing-digital",
    "graphic_design": "diseno",
    "design": "diseno",
    "design_studio": "diseno",
    "web_design": "diseno",
    "event_management": "eventos",
    "event_planner": "eventos",
    "public_relations": "comunicacion",
    "communication": "comunicacion",
    "communication_agency": "comunicacion",
    "film_production": "productoras",
    "video_production": "productoras",
    # Sectores opcionales (solo se guardan si el perfil los elige; ver relevance.py).
    "newspaper": "medios",
    "news_agency": "medios",
    "publisher": "editoriales",
    "photographer": "fotografia",
    "record_label": "musica",
}
CRAFT_TO_CATEGORY = {
    "graphic_design": "diseno",
    "design": "diseno",
    "web_design": "diseno",
    "photographer": "fotografia",
}
STUDIO_TO_CATEGORY = {
    "video": "productoras",
    "cinema": "productoras",
    "film": "productoras",
    "television": "productoras",
    "radio": "medios",
    "audio": "musica",
    "recording": "musica",
    "photography": "fotografia",
}
TOURISM_TO_CATEGORY = {"gallery": "cultura", "museum": "cultura"}
AMENITY_TO_CATEGORY = {"arts_centre": "cultura"}


def build_query(bbox: str = BARCELONA_BBOX) -> str:
    """Filtros por valor exacto (usan índice; los regex sobre áreas grandes acaban en 504)."""
    clauses = [f'  nwr["office"="{v}"];' for v in OFFICE_TO_CATEGORY]
    clauses += [f'  nwr["craft"="{v}"];' for v in CRAFT_TO_CATEGORY]
    clauses += [f'  nwr["amenity"="studio"]["studio"="{v}"];' for v in STUDIO_TO_CATEGORY]
    clauses += [f'  nwr["tourism"="{v}"];' for v in TOURISM_TO_CATEGORY]
    clauses += [f'  nwr["amenity"="{v}"];' for v in AMENITY_TO_CATEGORY]
    body = "\n".join(clauses)
    return f"[out:json][timeout:180][bbox:{bbox}];\n(\n{body}\n);\nout center;"


QUERY = build_query()


def category_for(tags: dict) -> str | None:
    if tags.get("office") in OFFICE_TO_CATEGORY:
        return OFFICE_TO_CATEGORY[tags["office"]]
    if tags.get("craft") in CRAFT_TO_CATEGORY:
        return CRAFT_TO_CATEGORY[tags["craft"]]
    if tags.get("amenity") == "studio":
        return STUDIO_TO_CATEGORY.get(tags.get("studio", ""))
    if tags.get("amenity") in AMENITY_TO_CATEGORY:
        return AMENITY_TO_CATEGORY[tags["amenity"]]
    return TOURISM_TO_CATEGORY.get(tags.get("tourism", ""))


def _address(tags: dict) -> str:
    street, number = tags.get("addr:street", ""), tags.get("addr:housenumber", "")
    if street and number:
        return f"{street}, {number}"
    return street or tags.get("addr:full", "")


def element_to_raw(element: dict) -> RawCompany | None:
    tags = element.get("tags") or {}
    name = (tags.get("name") or "").strip()
    if not name:
        return None
    center = element.get("center") or {}
    return RawCompany(
        source=Source.OSM,
        external_id=f"{element['type']}/{element['id']}",
        name=name,
        category_slug=category_for(tags),
        lat=element.get("lat", center.get("lat")),
        lng=element.get("lon", center.get("lon")),
        address=_address(tags),
        postcode=tags.get("addr:postcode", ""),
        city=tags.get("addr:city", "") or "Barcelona",
        website=tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
        phone=tags.get("phone") or tags.get("contact:phone") or "",
        email=tags.get("email") or tags.get("contact:email") or "",
        opening_hours=tags.get("opening_hours", ""),
        payload={"type": element["type"], "id": element["id"], "tags": tags},
    )


class OverpassAdapter:
    name = Source.OSM

    def __init__(self, url: str = OVERPASS_URL, ttl: timedelta = timedelta(days=7)):
        self.url = url
        self.ttl = ttl

    def _request(self) -> tuple[str, Fetched]:
        """Varios intentos en el principal con pausas crecientes; si sigue saturado, los espejos.

        Desde los runners de GitHub (IPs compartidas) los 504 son frecuentes; el cron
        semanal tolera esperar unos minutos.
        """
        last_error = ""
        urls = [self.url] * PRIMARY_ATTEMPTS + list(FALLBACK_URLS)
        for attempt, url in enumerate(urls, start=1):
            try:
                response = fetch(
                    url, method="POST", data={"data": QUERY}, ttl=self.ttl, timeout=200
                )
            except FetchError as exc:
                last_error = str(exc)
                continue
            if response.status_code == 200:
                return url, response
            if response.status_code == 406:
                raise SourceError("Overpass ha bloqueado el User-Agent (406).")
            last_error = f"Overpass {url} respondió {response.status_code}"
            if response.status_code in (429, 504) and attempt < len(urls):
                pause = RETRY_PAUSE_SECONDS * min(attempt, 3)
                logger.warning("%s; pausa de %ss", last_error, pause)
                time.sleep(pause)
        raise SourceError(f"Overpass saturado o caído: {last_error}")

    def fetch(self) -> Iterable[RawCompany]:
        url, response = self._request()
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceError("Overpass devolvió una respuesta que no es JSON.") from exc
        if not isinstance(body, dict):
            raise SourceError("Overpass devolvió un JSON inesperado.")
        # Timeout o falta de memoria en el servidor llegan con 200, un 'remark' y los
        # elementos truncados: tomarlos por completos retiraría empresas que siguen ahí.
        remark = str(body.get("remark") or "")
        if "error" in remark.lower():
            forget(url, method="POST", data={"data": QUERY})  # que no se reutilice 7 días
            raise SourceError(f"Overpass incompleto: {remark}")
        elements = body.get("elements", [])
        logger.info(
            "Overpass: %d elementos (%s)", len(elements), "caché" if response.cached else "red"
        )
        for element in elements:
            raw = element_to_raw(element)
            if raw is not None:
                yield raw
