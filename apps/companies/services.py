"""Ingesta de empresas: de `RawCompany` a `Company` + `SourceRecord`, con dedupe y fusión."""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.catalog.models import Category, Zone

from .dedupe import MemoryIndex, confidence_score, find_match, merge_into
from .models import Company, Source, SourceRecord
from .relevance import exclusion_reason, searched_sectors
from .sources import RawCompany, SourceError, get_adapters

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    matched_by: Counter = field(default_factory=Counter)
    skipped: int = 0
    excluded: Counter = field(default_factory=Counter)  # motivo -> descartadas (relevance.py)
    retired: int = 0  # registros de la fuente que ya no aparecen
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "matched_by": dict(self.matched_by),
            "skipped": self.skipped,
            "excluded": dict(self.excluded),
            "retired": self.retired,
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
    "is_active",
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
        self._seen: set[tuple[str, str]] = set()

    def add(self, raw: RawCompany) -> tuple[Company, str]:
        """Fusiona `raw` en memoria. Devuelve (empresa, 'created'|'updated:<criterio>')."""
        key = (raw.source, raw.external_id)
        self._seen.add(key)
        record = self.records.get(key)
        if record is not None:
            company, how = self.record_company[key], "record"
        else:
            company, how = find_match(raw, self.index)

        created = company is None
        if created:
            company = Company(name=raw.name, first_seen_at=timezone.now())

        merge_into(company, raw, category=self.categories.get(raw.category_slug or ""))
        if created:  # tras la fusión: si fallase, no quedaría una empresa a medias en el lote
            self._new_companies.append(company)
        manual = {f for f, s in (company.field_sources or {}).items() if s == Source.MANUAL}
        if "zone" not in manual:
            company.zone = zone_for(company.lat, company.lng, self.zones) or company.zone
        company.last_seen_at = timezone.now()
        if "is_active" not in manual:  # desactivada a mano en el admin: se respeta
            company.is_active = True  # una empresa retirada que reaparece vuelve a la lista
        self.index.add(company)
        if company.pk is not None:
            self._dirty[id(company)] = company

        if record is None:
            record = SourceRecord(
                company=company,
                source=raw.source,
                external_id=raw.external_id,
                name=raw.name[:200],
                payload=raw.payload,
            )
            self.records[key] = record
            self.record_company[key] = company
            self._new_records.append(record)
        else:
            record.name = raw.name[:200]
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

    def retire_unseen(self, source: str) -> int:
        """Tras una lectura completa de `source`, borra sus registros que ya no aparecen.

        Las empresas que se quedan sin ninguna fuente pasan a inactivas (no se borran:
        pueden tener favoritos o visitas); las demás recalculan su confianza.
        """
        self.flush()
        gone = [key for key in self.records if key[0] == source and key not in self._seen]
        if not gone:
            return 0
        affected: dict[int, Company] = {}
        record_ids: list[int] = []
        for key in gone:
            record = self.records.pop(key)
            company = self.record_company.pop(key)
            affected[id(company)] = company
            record_ids.append(record.pk)
        # Fuentes que les quedan, contadas desde los registros vivos: una empresa con
        # varios registros de la misma fuente la conserva aunque se retire uno.
        left: dict[int, set[str]] = defaultdict(set)
        for (src, _), company in self.record_company.items():
            if id(company) in affected:
                left[id(company)].add(src)
        for company in affected.values():
            self.sources[id(company)] = left[id(company)]
            remaining = len(left[id(company)])
            if source not in left[id(company)]:
                # Libera los campos de la fuente retirada: las que quedan podrán actualizarlos.
                company.field_sources = {
                    f: s for f, s in (company.field_sources or {}).items() if s != source
                }
            if (company.field_sources or {}).get("is_active") != Source.MANUAL:
                company.is_active = remaining > 0
            company.confidence_score = confidence_score(company, remaining)
            self._dirty[id(company)] = company
        with transaction.atomic():
            SourceRecord.objects.filter(pk__in=record_ids).delete()
            self.flush()
        return len(gone)


def ingest(
    raw: RawCompany, *, categories: dict[str, Category], zones: list[Zone]
) -> tuple[Company, str]:
    """Ingesta de una sola empresa (atajo para pruebas y usos puntuales)."""
    ingestor = Ingestor(categories=categories, zones=zones)
    result = ingestor.add(raw)
    ingestor.flush()
    return result


def run_discovery(
    source_names: list[str] | None = None,
    *,
    dry_run: bool = False,
    sectors: frozenset[str] | None = None,
) -> dict[str, IngestStats]:
    """Ejecuta cada fuente y vuelca sus resultados. Un fallo en una fuente no detiene las demás.

    Solo entra lo relevante (`relevance.exclusion_reason`) de los sectores buscados, que por
    defecto salen del perfil: lo descartado no se ve, así que `retire_unseen` lo retira.
    """
    profile = None
    if sectors is None:
        from apps.enrichment.profile import current_profile

        profile = current_profile()
        sectors = searched_sectors(profile)
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
                if reason := exclusion_reason(raw, sectors):
                    stats.excluded[reason] += 1
                    continue
                if ingestor is None:
                    continue
                try:
                    _, outcome = ingestor.add(raw)
                except DatabaseError:
                    raise  # fallo al volcar el lote: no es culpa de este registro
                except Exception:
                    # Un registro anómalo no debe tumbar la fuente ni las siguientes.
                    stats.skipped += 1
                    logger.exception("Fuente %s: omitido %s", adapter.name, raw.external_id)
                    continue
                if outcome == "created":
                    stats.created += 1
                else:
                    stats.updated += 1
                    stats.matched_by[outcome.split(":", 1)[1]] += 1
        except SourceError as exc:
            stats.error = str(exc)
            logger.error("Fuente %s: %s", adapter.name, exc)
        if ingestor is None:
            continue
        ingestor.flush()
        # Solo una lectura completa y con resultados autoriza a retirar lo que falta
        # (una fuente caída o vacía no debe vaciar la base de datos).
        if not stats.error and stats.fetched:
            stats.retired = ingestor.retire_unseen(adapter.name)
    if profile is not None and ingestor is not None:
        # Perfil ya atendido: guardarlo sin cambiar sectores no lanza otra búsqueda.
        type(profile).objects.filter(pk=profile.pk).update(discovered_sectors=sorted(sectors))
    return results


def discovery_summary(results: dict[str, IngestStats]) -> tuple[str, list[str]]:
    """Resumen legible por fuente y la lista de errores (para el JobRun)."""
    lines = []
    for name, s in results.items():
        if s.error:
            lines.append(f"{name}: ERROR {s.error}")
            continue
        line = f"{name}: {s.fetched} encontradas, {s.created} nuevas, {s.updated} actualizadas"
        if excluded := sum(s.excluded.values()):
            line += f", {excluded} descartadas"
        if s.retired:
            line += f", {s.retired} retiradas"
        if s.skipped:
            line += f", {s.skipped} omitidas por error"
        lines.append(line)
    errors = [f"{n}: {s.error}" for n, s in results.items() if s.error]
    return "\n".join(lines), errors
