"""Interfaz común de las fuentes de descubrimiento.

Añadir una fuente = una clase que implemente `SourceAdapter.fetch()` y
devuelva `RawCompany` normalizados. La fusión y la deduplicación viven en
`apps.companies.dedupe`, comunes a todas.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol


class SourceError(Exception):
    """Fallo de una fuente (red, cuota, formato). El comando lo registra y sigue con las demás."""


@dataclass
class RawCompany:
    source: str  # apps.companies.models.Source
    external_id: str
    name: str
    category_slug: str | None = None  # slug de apps.catalog.Category
    lat: float | None = None
    lng: float | None = None
    address: str = ""
    postcode: str = ""
    city: str = ""
    website: str = ""
    phone: str = ""
    email: str = ""
    opening_hours: str = ""
    rating: float | None = None
    rating_count: int | None = None
    payload: dict = field(default_factory=dict)  # respuesta cruda de la fuente


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> Iterable[RawCompany]:
        """Devuelve todas las empresas candidatas de Barcelona que conoce la fuente."""
        ...
