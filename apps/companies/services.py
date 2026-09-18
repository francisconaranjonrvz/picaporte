"""Ingesta de empresas: de `RawCompany` a `Company` + `SourceRecord`, con dedupe y fusión."""

import logging
from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Category, Zone

from .dedupe import confidence_score, find_match, merge_into
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


@transaction.atomic
def ingest(
    raw: RawCompany, *, categories: dict[str, Category], zones: list[Zone]
) -> tuple[Company, str]:
    """Crea o actualiza la empresa que describe `raw`. Devuelve (empresa, 'created'|'updated')."""
    category = categories.get(raw.category_slug or "")
    record = (
        SourceRecord.objects.select_related("company")
        .filter(source=raw.source, external_id=raw.external_id)
        .first()
    )

    if record is not None:
        company, how = record.company, "record"
    else:
        company, how = find_match(raw)

    created = company is None
    if created:
        company = Company(name=raw.name, first_seen_at=timezone.now())
        how = "new"

    merge_into(company, raw, category=category)
    company.zone = zone_for(company.lat, company.lng, zones) or company.zone
    company.last_seen_at = timezone.now()
    company.save()

    if record is None:
        SourceRecord.objects.create(
            company=company,
            source=raw.source,
            external_id=raw.external_id,
            name=raw.name,
            payload=raw.payload,
        )
    else:
        record.name = raw.name
        record.payload = raw.payload
        record.fetched_at = timezone.now()
        record.save(update_fields=["name", "payload", "fetched_at"])

    source_count = company.records.values("source").distinct().count()
    company.confidence_score = confidence_score(company, source_count)
    company.save(update_fields=["confidence_score"])
    return company, ("created" if created else f"updated:{how}")


def run_discovery(
    source_names: list[str] | None = None, *, dry_run: bool = False
) -> dict[str, IngestStats]:
    """Ejecuta cada fuente y vuelca sus resultados. Un fallo en una fuente no detiene las demás."""
    categories = {c.slug: c for c in Category.objects.all()}
    zones = list(Zone.objects.filter(is_active=True))
    results: dict[str, IngestStats] = {}
    for adapter in get_adapters(source_names):
        stats = IngestStats()
        results[adapter.name] = stats
        try:
            for raw in adapter.fetch():
                stats.fetched += 1
                if dry_run:
                    continue
                _, outcome = ingest(raw, categories=categories, zones=zones)
                if outcome == "created":
                    stats.created += 1
                else:
                    stats.updated += 1
                    stats.matched_by[outcome.split(":", 1)[1]] += 1
        except SourceError as exc:
            stats.error = str(exc)
            logger.error("Fuente %s: %s", adapter.name, exc)
    return results
