"""Ingesta de empresas: de `RawCompany` a `Company` + `SourceRecord`, con dedupe y fusión."""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Category, Zone

from .dedupe import MemoryIndex, confidence_score, find_match, merge_into
from .models import Company, SourceRecord
from .sources import RawCompany, SourceError, get_adapters

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    matched_by: Counter = field(default_factory=Counter)
    skipped: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "matched_by": dict(self.matched_by),
            "skipped": self.skipped,
            "error": self.error,
        }


def zone_for(lat: float | None, lng: float | None, zones: list[Zone]) -> Zone | None:
    """Zona más pequeña cuyo bbox [lng_min, lat_min, lng_max, lat_max] contiene el punto.

    Los bbox se solapan (el Eixample abarca parte de Gràcia): gana la más específica.
    """
    if lat is None or lng is None:
        return None
    best, best_area = None, float("inf")
    for zone in zones:
        if len(zone.bbox) != 4:
            continue
        lng_min, lat_min, lng_max, lat_max = zone.bbox
        if lng_min <= lng <= lng_max and lat_min <= lat <= lat_max:
            area = (lng_max - lng_min) * (lat_max - lat_min)
            if area < best_area:
                best, best_area = zone, area
    return best


# Campos que una fusión puede cambiar en una empresa existente (para bulk_update).
MUTABLE_FIELDS = [
    "name",
    "category",
    "zone",
    "address",
    "postcode",
    "city",
    "lat",
    "lng",
    "website",
    "domain",
    "phone",
    "email",
    "opening_hours",
    "rating",
    "rating_count",
    "confidence_score",
    "field_sources",
    "last_seen_at",
]
FLUSH_EVERY = 500


class Ingestor:
    """Ingesta por lotes: índice en memoria y escrituras masivas.

    El worker corre en GitHub Actions (EE. UU.) y Neon en Frankfurt: cada consulta
    cuesta ~100 ms, así que nada se consulta por empresa. Se cargan una vez las
    empresas y los registros, se decide todo en memoria y se escribe con
    `bulk_create`/`bulk_update` cada `flush_every` empresas.
    """

    def __init__(
        self, *, categories: dict[str, Category], zones: list[Zone], flush_every=FLUSH_EVERY
    ):
        self.categories = categories
        self.zones = zones
        self.flush_every = flush_every
        companies = {c.pk: c for c in Company.objects.select_related("category", "zone")}
        self.index = MemoryIndex(companies.values())
        self.records: dict[tuple[str, str], SourceRecord] = {}
        self.record_company: dict[tuple[str, str], Company] = {}
        # Fuentes distintas por empresa (clave: id() del objeto, válido también antes de guardarla).
        self.sources: dict[int, set[str]] = defaultdict(set)
        for record in SourceRecord.objects.only("id", "company_id", "source", "external_id"):
            key = (record.source, record.external_id)
            company = companies[record.company_id]
            self.records[key] = record
            self.record_company[key] = company
            self.sources[id(company)].add(record.source)
        self._new_companies: list[Company] = []
        self._dirty: dict[int, Company] = {}
        self._new_records: list[SourceRecord] = []
        self._touched_records: dict[int, SourceRecord] = {}
        self._pending = 0

    def add(self, raw: RawCompany) -> tuple[Company, str]:
        """Fusiona `raw` en memoria. Devuelve (empresa, 'created'|'updated:<criterio>')."""
        key = (raw.source, raw.external_id)
        record = self.records.get(key)
        if record is not None:
            company, how = self.record_company[key], "record"
        else:
            company, how = find_match(raw, self.index)

        created = company is None
        if created:
            company = Company(name=raw.name, first_seen_at=timezone.now())
            self._new_companies.append(company)

        merge_into(company, raw, category=self.categories.get(raw.category_slug or ""))
        company.zone = zone_for(company.lat, company.lng, self.zones) or company.zone
        company.last_seen_at = timezone.now()
        self.index.add(company)
        if company.pk is not None:
            self._dirty[id(company)] = company

        if record is None:
            record = SourceRecord(
                company=company,
                source=raw.source,
                external_id=raw.external_id,
                name=raw.name,
                payload=raw.payload,
            )
            self.records[key] = record
            self.record_company[key] = company
            self._new_records.append(record)
        else:
            record.name = raw.name
            record.payload = raw.payload
            record.fetched_at = timezone.now()
            if record.pk is not None:
                self._touched_records[id(record)] = record

        self.sources[id(company)].add(raw.source)
        company.confidence_score = confidence_score(company, len(self.sources[id(company)]))

        self._pending += 1
        if self._pending >= self.flush_every:
            self.flush()
        return company, ("created" if created else f"updated:{how}")

    @transaction.atomic
    def flush(self) -> None:
        Company.objects.bulk_create(self._new_companies, batch_size=FLUSH_EVERY)
        Company.objects.bulk_update(self._dirty.values(), MUTABLE_FIELDS, batch_size=FLUSH_EVERY)
        SourceRecord.objects.bulk_create(self._new_records, batch_size=FLUSH_EVERY)
        SourceRecord.objects.bulk_update(
            self._touched_records.values(),
            ["name", "payload", "fetched_at"],
            batch_size=FLUSH_EVERY,
        )
        self._new_companies, self._new_records = [], []
        self._dirty, self._touched_records = {}, {}
        self._pending = 0


def ingest(
    raw: RawCompany, *, categories: dict[str, Category], zones: list[Zone]
) -> tuple[Company, str]:
    """Ingesta de una sola empresa (atajo para pruebas y usos puntuales)."""
    ingestor = Ingestor(categories=categories, zones=zones)
    result = ingestor.add(raw)
    ingestor.flush()
    return result


def run_discovery(
    source_names: list[str] | None = None, *, dry_run: bool = False
) -> dict[str, IngestStats]:
    """Ejecuta cada fuente y vuelca sus resultados. Un fallo en una fuente no detiene las demás."""
    categories = {c.slug: c for c in Category.objects.all()}
    zones = list(Zone.objects.filter(is_active=True))
    ingestor = None if dry_run else Ingestor(categories=categories, zones=zones)
    results: dict[str, IngestStats] = {}
    for adapter in get_adapters(source_names):
        stats = IngestStats()
        results[adapter.name] = stats
        try:
            for raw in adapter.fetch():
                stats.fetched += 1
                if ingestor is None:
                    continue
                _, outcome = ingestor.add(raw)
                if outcome == "created":
                    stats.created += 1
                else:
                    stats.updated += 1
                    stats.matched_by[outcome.split(":", 1)[1]] += 1
        except SourceError as exc:
            stats.error = str(exc)
            logger.error("Fuente %s: %s", adapter.name, exc)
        if ingestor is not None:
            ingestor.flush()
    return results
