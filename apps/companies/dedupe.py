"""Deduplicación y fusión de empresas procedentes de varias fuentes.

Regla de coincidencia (en este orden): mismo registro de fuente ya conocido;
mismo dominio web normalizado; nombre muy parecido (rapidfuzz) a menos de
100 m. La fusión es campo a campo con prioridad por fuente y guarda la
procedencia en `Company.field_sources`.
"""

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from .models import SOURCE_PRIORITY, Company
from .sources.base import RawCompany

NAME_SIMILARITY_MIN = 90  # 0-100, token_set_ratio
MAX_DISTANCE_M = 100
DEGREE_MARGIN = 0.002  # ~200 m en latitud; recorte grosero antes de calcular distancias

LEGAL_SUFFIXES = re.compile(
    r"\b(s\.?l\.?u?|s\.?a\.?|s\.?c\.?p\.?|s\.?l\.?l\.?|slu|sl|sa|scp|ltd|inc|gmbh|bcn|barcelona)\b\.?",
    re.IGNORECASE,
)


def normalize_domain(url: str) -> str:
    """'https://www.Foo.com/about?x' -> 'foo.com'. Cadena vacía si no hay host."""
    if not url:
        return ""
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    host = (urlsplit(candidate).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = LEGAL_SUFFIXES.sub(" ", text.lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def name_similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(normalize_name(a), normalize_name(b))


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine en metros."""
    r = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _cell(lat: float, lng: float) -> tuple[int, int]:
    return (math.floor(lat / DEGREE_MARGIN), math.floor(lng / DEGREE_MARGIN))


class DbIndex:
    """Candidatos consultando la BD (una consulta por búsqueda): para ingestas sueltas."""

    def by_domain(self, domain: str) -> list[Company]:
        return list(Company.objects.filter(domain=domain))

    def nearby(self, lat: float, lng: float) -> list[Company]:
        return list(
            Company.objects.filter(
                lat__gte=lat - DEGREE_MARGIN,
                lat__lte=lat + DEGREE_MARGIN,
                lng__gte=lng - DEGREE_MARGIN,
                lng__lte=lng + DEGREE_MARGIN,
            )
        )


class MemoryIndex:
    """Candidatos en memoria (dominio + rejilla de ~200 m) para ingestas por lotes.

    Se carga una vez y se mantiene al día con `add()`, de modo que una empresa creada
    en el mismo lote ya cuenta para las siguientes.
    """

    def __init__(self, companies: Iterable[Company] = ()):
        self._domain: dict[str, list[Company]] = defaultdict(list)
        self._grid: dict[tuple[int, int], list[Company]] = defaultdict(list)
        for company in companies:
            self.add(company)

    @classmethod
    def load(cls) -> "MemoryIndex":
        return cls(Company.objects.all())

    @staticmethod
    def _put(bucket: list[Company], company: Company) -> None:
        if not any(existing is company for existing in bucket):
            bucket.append(company)

    def add(self, company: Company) -> None:
        """Indexa (o reindexa tras una fusión que cambie dominio o coordenadas)."""
        if company.domain:
            self._put(self._domain[company.domain], company)
        if company.lat is not None and company.lng is not None:
            self._put(self._grid[_cell(company.lat, company.lng)], company)

    def by_domain(self, domain: str) -> list[Company]:
        return list(self._domain.get(domain, ()))

    def nearby(self, lat: float, lng: float) -> list[Company]:
        row, col = _cell(lat, lng)
        found: list[Company] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                found.extend(self._grid.get((row + dr, col + dc), ()))
        return found


def find_match(
    raw: RawCompany, index: DbIndex | MemoryIndex | None = None
) -> tuple[Company | None, str]:
    """Devuelve (empresa, criterio) o (None, '') si no hay coincidencia."""
    index = index or DbIndex()
    domain = normalize_domain(raw.website)
    if domain:
        by_domain = index.by_domain(domain)
        if by_domain:
            if raw.lat is not None and raw.lng is not None:
                by_domain.sort(key=lambda c: _distance_or_inf(c, raw))
            return by_domain[0], "domain"

    if raw.lat is None or raw.lng is None:
        return None, ""
    best, best_score = None, 0.0
    for company in index.nearby(raw.lat, raw.lng):
        if _distance_or_inf(company, raw) > MAX_DISTANCE_M:
            continue
        score = name_similarity(company.name, raw.name)
        if score >= NAME_SIMILARITY_MIN and score > best_score:
            best, best_score = company, score
    return (best, "name+distance") if best else (None, "")


def _distance_or_inf(company: Company, raw: RawCompany) -> float:
    if company.lat is None or company.lng is None or raw.lat is None or raw.lng is None:
        return math.inf
    return distance_m(company.lat, company.lng, raw.lat, raw.lng)


def merge_into(company: Company, raw: RawCompany, category=None) -> list[str]:
    """Aplica los campos de `raw` a la empresa según prioridad de fuente. Devuelve los cambiados."""
    values = {
        "address": raw.address,
        "postcode": raw.postcode,
        "city": raw.city,
        "website": raw.website,
        "domain": normalize_domain(raw.website),
        "phone": raw.phone,
        "email": raw.email,
        "opening_hours": raw.opening_hours,
        "rating": raw.rating,
        "rating_count": raw.rating_count,
        "lat": raw.lat,
        "lng": raw.lng,
        "category": category,
    }
    priority = SOURCE_PRIORITY.get(raw.source, 0)
    sources = dict(company.field_sources or {})
    changed: list[str] = []
    for field_name, value in values.items():
        if value in (None, ""):
            continue
        current = getattr(company, field_name)
        owner = sources.get(field_name, "")
        current_priority = SOURCE_PRIORITY.get(owner, -1)
        # Gana la fuente más prioritaria; la misma fuente siempre puede actualizar su propio dato.
        if current in (None, "") or priority > current_priority or owner == raw.source:
            if current != value:
                setattr(company, field_name, value)
                changed.append(field_name)
            sources[field_name] = raw.source
    # El nombre lo fija la fuente más prioritaria (o el primero que llegó).
    name_owner = sources.get("name", "")
    if (
        priority > SOURCE_PRIORITY.get(name_owner, -1) or name_owner == raw.source
    ) and company.name != raw.name:
        company.name = raw.name
        changed.append("name")
        sources["name"] = raw.source
    sources.setdefault("name", raw.source)
    company.field_sources = sources
    return changed


def confidence_score(company: Company, source_count: int) -> int:
    """0-100: nº de fuentes independientes + completitud de los datos de contacto."""
    score = 15 + 20 * min(
        source_count, 3
    )  # 35 / 55 / 75 por 1 / 2 / 3+ fuentes; el resto, contacto
    score += 10 if company.website else 0
    score += 8 if company.phone else 0
    score += 6 if company.address else 0
    score += 6 if company.lat is not None and company.lng is not None else 0
    score += 5 if company.category_id else 0
    return min(score, 100)
