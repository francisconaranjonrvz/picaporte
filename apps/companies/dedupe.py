"""Deduplicación y fusión de empresas procedentes de varias fuentes.

Regla de coincidencia (en este orden): mismo registro de fuente ya conocido;
mismo dominio web normalizado a menos de 500 m (otras sedes de una cadena son
otras empresas; perfiles en redes sociales no cuentan como dominio); nombre
muy parecido (rapidfuzz) a menos de 100 m. La fusión es campo a campo con
prioridad por fuente y guarda la procedencia en `Company.field_sources`.
"""

import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from .models import SOURCE_PRIORITY, Company, Source
from .sources.base import RawCompany

NAME_SIMILARITY_MIN = 90  # 0-100, token_set_ratio
MAX_DISTANCE_M = 100
# Mismo dominio pero más lejos que esto = otra sede de la cadena, no la misma empresa.
# Holgado para absorber el desfase entre las coordenadas de OSM y las de Foursquare.
DOMAIN_MAX_DISTANCE_M = 500
DEGREE_MARGIN = 0.002  # ~200 m en latitud; recorte grosero antes de calcular distancias
WEBSITE_MAX_LENGTH = 300  # Company.website

LEGAL_SUFFIXES = re.compile(
    r"\b(s\.?l\.?u?|s\.?a\.?|s\.?c\.?p\.?|s\.?l\.?l\.?|slu|sl|sa|scp|ltd|inc|gmbh|bcn|barcelona)\b\.?",
    re.IGNORECASE,
)
# Palabras del sector y de relleno: un nombre hecho solo de ellas ("Events", "Coworking
# Barcelona") no identifica a nadie y no debe fusionarse por nombre.
_GENERIC_WORDS = """
    a al and d de del el els en i l la las les los of the un una y
    agencia agency agencies estudi estudio studio studios taller lab
    design disseny diseno disenyo grafic grafico grafica graphic creative creativa creativo
    marketing digital online web media comunicacio comunicacion communication communications
    publicitat publicidad advertising pr rrpp relacions relaciones public
    event events evento eventos esdeveniments
    cowork coworking space spaces espacio espai hub office oficina oficinas centre center centro
    work working business group grup grupo services serveis servicios solutions consulting
    film films video audiovisual audiovisuals produccions producciones productora production
    productions
"""
GENERIC_NAME_WORDS = frozenset(_GENERIC_WORDS.split())
# Hosts donde muchas empresas distintas tienen "web" (perfiles sociales, agregadores,
# constructores de páginas): su dominio no identifica a una empresa.
SHARED_HOSTS = frozenset(
    {
        "instagram.com",
        "linkedin.com",
        "facebook.com",
        "fb.com",
        "x.com",
        "twitter.com",
        "tiktok.com",
        "youtube.com",
        "vimeo.com",
        "behance.net",
        "linktr.ee",
        "linkin.bio",
        "wa.me",
        "google.com",
        "goo.gl",
        "business.site",
        "wixsite.com",
        "blogspot.com",
        "wordpress.com",
        "tripadvisor.com",
        "tripadvisor.es",
    }
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+\-]*:", re.IGNORECASE)
_HOST_PORT = re.compile(r"^[^:/]+:\d")
_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f]")


def normalize_website(url: str) -> str:
    """Web segura para usar como enlace: http(s) con host, o cadena vacía.

    'www.x.com' -> 'https://www.x.com'; 'javascript:...', 'mailto:...' o una URL
    rota -> ''. Se quitan espacios y caracteres de control (el navegador los
    ignora, así que 'java\\nscript:' también sería 'javascript:').
    """
    candidate = _UNSAFE_CHARS.sub("", url or "")
    if not candidate:
        return ""
    if "://" not in candidate:
        if _SCHEME.match(candidate) and not _HOST_PORT.match(candidate):
            return ""  # otro esquema (javascript:, mailto:, tel:...)
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return ""
    if len(candidate) > WEBSITE_MAX_LENGTH:
        return ""  # recortada dejaría de funcionar
    return candidate


def normalize_domain(url: str) -> str:
    """'https://www.Foo.com/about?x' -> 'foo.com'. Cadena vacía si no hay host."""
    if not url:
        return ""
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        host = (urlsplit(candidate).hostname or "").lower()
    except ValueError:  # p. ej. 'http://[roto.com'
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def is_shared_host(domain: str) -> bool:
    return any(domain == h or domain.endswith(f".{h}") for h in SHARED_HOSTS)


def company_domain(url: str) -> str:
    """Dominio que identifica a la empresa: vacío si la web es un perfil en un host compartido."""
    domain = normalize_domain(url)
    return "" if is_shared_host(domain) else domain


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = LEGAL_SUFFIXES.sub(" ", text.lower())
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return " ".join(text.split())


def _is_generic(normalized: str) -> bool:
    return all(token in GENERIC_NAME_WORDS for token in normalized.split())


def name_similarity(a: str, b: str) -> float:
    """token_set_ratio, salvo que un nombre sea solo palabras genéricas (entonces 0).

    token_set_ratio da 100 si un nombre está contenido en el otro: sin esta regla
    "Events" casaría con cualquier "Kiwi Events" del mismo portal.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if _is_generic(na) or _is_generic(nb):
        return 0.0
    return fuzz.token_set_ratio(na, nb)


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
    domain = company_domain(normalize_website(raw.website))
    if domain:
        by_domain = index.by_domain(domain)
        if by_domain and (raw.lat is None or raw.lng is None):
            return by_domain[0], "domain"
        # El más cercano, si está a distancia de ser la misma sede; sin coordenadas en la
        # empresa no se puede comprobar y vale el dominio.
        by_domain.sort(key=lambda c: _distance_or_inf(c, raw))
        for company in by_domain:
            no_coords = company.lat is None or company.lng is None
            if no_coords or _distance_or_inf(company, raw) <= DOMAIN_MAX_DISTANCE_M:
                return company, "domain"

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


def first_value(value: str) -> str:
    """OSM separa varios valores con ';' ('+34 93...;+34 600...'): nos quedamos con el primero."""
    return (value or "").split(";", 1)[0].strip()


def clean_postcode(value: str) -> str:
    """'08005 Barcelona' -> '08005'; si no hay 5 cifras, el primer valor tal cual."""
    match = re.search(r"\b\d{5}\b", value or "")
    return match.group(0) if match else first_value(value)


def fit(field_name: str, value: str) -> str:
    """Recorta `value` al max_length del campo de Company."""
    max_length = Company._meta.get_field(field_name).max_length
    return value[:max_length] if max_length else value


def merge_into(company: Company, raw: RawCompany, category=None) -> list[str]:
    """Aplica los campos de `raw` a la empresa según prioridad de fuente. Devuelve los cambiados."""
    website = normalize_website(raw.website)
    values = {
        "address": raw.address,
        "postcode": clean_postcode(raw.postcode),
        "city": raw.city,
        "website": website,
        "domain": company_domain(website),
        "phone": first_value(raw.phone),
        "email": first_value(raw.email),
        "opening_hours": raw.opening_hours,
        "rating": raw.rating,
        "rating_count": raw.rating_count,
        "lat": raw.lat,
        "lng": raw.lng,
        "category": category,
    }
    # bulk_create no valida longitudes y Postgres rechaza el lote entero si una se pasa.
    for field_name, value in values.items():
        if isinstance(value, str):
            values[field_name] = fit(field_name, value)
    raw_name = fit("name", raw.name)
    priority = SOURCE_PRIORITY.get(raw.source, 0)
    sources = dict(company.field_sources or {})
    changed: list[str] = []
    for field_name, value in values.items():
        if value in (None, ""):
            continue
        current = getattr(company, field_name)
        owner = sources.get(field_name, "")
        if owner == Source.MANUAL:
            continue  # editado en el admin, aunque sea para vaciarlo
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
    ) and company.name != raw_name:
        company.name = raw_name
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
