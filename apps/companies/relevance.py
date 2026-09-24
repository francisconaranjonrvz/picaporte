"""Qué se busca y qué se descarta al descubrir empresas.

Las fuentes devuelven todo lo que encaja con sus etiquetas; aquí se decide qué
entra en la base de datos:

- **Sectores**: los básicos (agencias, estudios, productoras...) siempre; los
  opcionales (medios, editoriales, fotografía...) solo si alguna cuenta los elige.
- **Término municipal**: el recuadro de las fuentes incluye L'Hospitalet,
  Cornellà o El Prat; se comprueba contra el límite real de Barcelona (OSM).
- **Nombres que delatan otra actividad**: imprentas, rotulistas, telecos,
  interiorismo, coworkings y centros de negocios, que se cuelan por palabras
  clave como "gràfiques", "comunicacions" o "disseny".

Lo descartado no llega a la ingesta, así que `retire_unseen` retira lo que ya
estuviera guardado (sin borrarlo: pasa a inactivo).
"""

import json
import re
import unicodedata
from functools import cache
from pathlib import Path

from .sources import RawCompany

CORE_SECTORS = frozenset(
    {"publicidad", "comunicacion", "marketing-digital", "diseno", "eventos", "productoras"}
)
OPTIONAL_SECTORS = frozenset({"medios", "editoriales", "fotografia", "musica", "cultura"})
ALL_SECTORS = CORE_SECTORS | OPTIONAL_SECTORS

BOUNDARY = Path(__file__).resolve().parent / "data" / "barcelona.json"

JUNK_NAME_RULES: list[tuple[str, re.Pattern]] = [
    (
        "imprenta o rótulos",
        re.compile(
            r"impre(m|n)ta|impressi|imprimir|copister|reprograf|\bprint(ing|er|s)?\b|serigraf"
            r"|r[oò]tul|retol|senyal|se[nñ]al[eé]tic|gr[aà]fiques|gr[aá]ficas|arts? gr[aà]fi"
            r"|artes gr[aá]fi|producci[oó]n? gr[aà]fica|subministr|suministro",
            re.I,
        ),
    ),
    (
        "otra actividad",
        re.compile(
            r"telecom|comunicacions? i software|software|inform[aà]tic|electr[oò]nic"
            r"|interior|arquitect|reform|immobili|inmobili|gestor[ií]a|assessor|asesor[ií]a"
            r"|advocat|abogad|notar",
            re.I,
        ),
    ),
    (
        "coworking o centro de negocios",
        re.compile(
            r"co-?work|business cent|work caf|centro de negocios|centre de negocis"
            r"|oficinas? virtual|\bwework\b|^spaces\b|\bregus\b",
            re.I,
        ),
    ),
]
# Nombres que son solo la palabra del sector ("Events"): no identifican a ninguna empresa.
GENERIC_NAMES = frozenset(
    {"events", "eventos", "esdeveniments", "design", "disseny", "diseno", "publicidad",
     "publicitat", "marketing", "comunicacion", "comunicacio", "productora", "produccions"}
)  # fmt: skip


@cache
def _ring() -> list[tuple[float, float]]:
    return [tuple(p) for p in json.loads(BOUNDARY.read_text(encoding="utf-8"))["ring"]]


def in_barcelona(lat: float, lng: float) -> bool:
    """Punto en el polígono del término municipal (ray casting, lng/lat)."""
    ring = _ring()
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


def junk_reason(name: str) -> str | None:
    if _plain(name) in GENERIC_NAMES:
        return "nombre genérico"
    for reason, pattern in JUNK_NAME_RULES:
        if pattern.search(name):
            return reason
    return None


def exclusion_reason(raw: RawCompany, sectors: frozenset[str]) -> str | None:
    """Motivo por el que `raw` no entra en la base de datos, o None si entra."""
    if raw.category_slug not in sectors:
        return "sector no buscado"
    if raw.lat is not None and raw.lng is not None and not in_barcelona(raw.lat, raw.lng):
        return "fuera de Barcelona"
    return junk_reason(raw.name)


def searched_sectors(profile=None) -> frozenset[str]:
    """Sectores básicos más los opcionales que el perfil haya marcado."""
    if profile is None or profile.pk is None:
        return CORE_SECTORS
    chosen = set(profile.categories.values_list("slug", flat=True))
    return CORE_SECTORS | (chosen & OPTIONAL_SECTORS)


def all_searched_sectors() -> frozenset[str]:
    """Lo que busca el descubrimiento: los básicos y los opcionales de cualquier cuenta.

    El catálogo es común: si una cuenta deja de querer "medios" pero otra no, esas
    empresas se quedan.
    """
    from apps.profiles.models import Profile

    chosen = set(
        Profile.objects.filter(categories__slug__in=OPTIONAL_SECTORS).values_list(
            "categories__slug", flat=True
        )
    )
    return CORE_SECTORS | (chosen & OPTIONAL_SECTORS)
