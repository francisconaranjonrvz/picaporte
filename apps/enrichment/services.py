"""Casos de uso del enriquecimiento: rastrear, extraer con IA y puntuar el encaje.

Todo corre en GitHub Actions (`manage.py enrich`), nunca en una request de Vercel.
El rastreo va en paralelo por dominios (solo red); las llamadas a la IA, en un
pool pequeño (el nivel gratuito de NVIDIA limita peticiones por minuto) y cada
una queda cacheada en `LLMCall`. Las escrituras de datos van en el hilo principal.
"""

import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import DatabaseError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.companies.models import Company
from apps.llm.client import LLMError, LLMNotConfigured, call_structured
from apps.llm.models import LLMCall
from apps.llm.prompts import load_prompt
from apps.profiles.models import Profile

from .crawler import CrawlError, CrawlResult, crawl_site
from .models import CompanyPage, Enrichment
from .profile import (
    SCORE_PROMPT,
    current_profile,
    profile_brief,
    profile_fingerprint,
    profile_is_usable,
)
from .schemas import ExtractedCompany, ScoreBatch

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = "enrich_extract_v1"
REFRESH_AFTER = timedelta(days=30)  # volver a rastrear webs con más de un mes
PAGE_CHAR_BUDGET = {"home": 6_000}  # el resto de páginas, DEFAULT_PAGE_CHARS
DEFAULT_PAGE_CHARS = 3_000
SCORE_BATCH_SIZE = 10
CRAWL_WORKERS = 8
LLM_WORKERS = 3
MAX_CONSECUTIVE_LLM_ERRORS = 5  # la API está caída o limitando: mejor parar y reintentar otro día


@dataclass
class EnrichStats:
    crawled: int = 0
    crawl_status: Counter = field(default_factory=Counter)
    extracted: int = 0
    scored: int = 0
    llm_errors: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "crawled": self.crawled,
            "crawl_status": dict(self.crawl_status),
            "extracted": self.extracted,
            "scored": self.scored,
            "llm_errors": self.llm_errors,
            "notes": self.notes,
        }

    def summary(self) -> str:
        ok = self.crawl_status.get(Enrichment.CrawlStatus.OK, 0)
        lines = [
            f"webs: {self.crawled} rastreadas ({ok} con contenido)",
            f"IA: {self.extracted} analizadas, {self.scored} puntuadas"
            + (f", {self.llm_errors} errores" if self.llm_errors else ""),
        ]
        return "\n".join(lines + self.notes)


class Deadline:
    def __init__(self, minutes: float | None):
        self.end = time.monotonic() + minutes * 60 if minutes else None

    @property
    def passed(self) -> bool:
        return self.end is not None and time.monotonic() >= self.end


def run_concurrently[T, R](
    fn: Callable[[T], R], items: Iterable[T], workers: int
) -> Iterable[tuple[T, R | Exception]]:
    """(item, resultado o excepción). Con `workers <= 1` corre en el hilo actual (tests).

    Cada hilo que toca la BD cierra su conexión al terminar la tarea.
    """
    items = list(items)
    if workers <= 1:
        for item in items:
            try:
                yield item, fn(item)
            except Exception as exc:
                yield item, exc
        return

    def task(item):
        try:
            return fn(item)
        except Exception as exc:
            return exc
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from zip(items, pool.map(task, items), strict=True)


# --- Rastreo -------------------------------------------------------------------------------------


def ensure_enrichments() -> int:
    """Crea el registro de enriquecimiento de las empresas activas que aún no lo tienen."""
    missing = Company.objects.filter(is_active=True, enrichment__isnull=True)
    rows = [
        Enrichment(
            company=company,
            crawl_status=(
                Enrichment.CrawlStatus.PENDING
                if company.website
                else Enrichment.CrawlStatus.NO_WEBSITE
            ),
        )
        for company in missing.only("id", "website")
    ]
    Enrichment.objects.bulk_create(rows, batch_size=500)
    return len(rows)


def select_for_crawl(limit: int, profile: Profile | None = None) -> list[Enrichment]:
    """Webs pendientes o caducadas, primero las de categorías preferidas y más confianza."""
    stale = timezone.now() - REFRESH_AFTER
    qs = (
        Enrichment.objects.select_related("company", "company__category")
        .filter(company__is_active=True)
        .exclude(company__website="")
        .filter(Q(crawled_at__isnull=True) | Q(crawled_at__lt=stale))
    )
    preferred = list(profile.categories.values_list("pk", flat=True)) if profile else []
    ordered = sorted(
        qs,
        key=lambda e: (
            e.company.category_id not in preferred,
            -e.company.confidence_score,
            e.company_id,
        ),
    )
    return ordered[:limit]


def pages_hash(pages: Iterable) -> str:
    digest = hashlib.sha256()
    for page in pages:
        for part in (page.kind, page.url, page.text):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


@transaction.atomic
def save_crawl(enrichment: Enrichment, outcome: CrawlResult | Exception) -> None:
    now = timezone.now()
    enrichment.crawled_at = now
    if isinstance(outcome, CrawlResult):
        CompanyPage.objects.filter(company_id=enrichment.company_id).delete()
        unique = {page.url[:500]: page for page in reversed(outcome.pages)}  # gana la primera
        CompanyPage.objects.bulk_create(
            CompanyPage(
                company_id=enrichment.company_id,
                url=url,
                kind=page.kind,
                status_code=page.status_code,
                lang=page.lang,
                text=page.text,
                fetched_at=now,
            )
            for url, page in reversed(unique.items())
        )
        enrichment.crawl_status = Enrichment.CrawlStatus.OK
        enrichment.crawl_error = ""
        enrichment.extraction_attempts = 0  # tras cada rastreo, la extracción vuelve a probar
        enrichment.pages_hash = pages_hash(outcome.pages)
        enrichment.emails = outcome.emails
        enrichment.socials = outcome.socials
    else:
        status = outcome.status if isinstance(outcome, CrawlError) else "unreachable"
        enrichment.crawl_status = status
        enrichment.crawl_error = str(outcome)[:300]
    enrichment.save()


def crawl(enrichments: list[Enrichment], stats: EnrichStats, deadline: Deadline, workers: int):
    websites = {e.pk: e.company.website for e in enrichments}
    chunk = max(workers, 1) * 4
    for start in range(0, len(enrichments), chunk):
        if deadline.passed:
            stats.notes.append(
                "Tiempo agotado durante el rastreo; se sigue en la próxima ejecución."
            )
            return
        batch = enrichments[start : start + chunk]
        for enrichment, outcome in run_concurrently(
            lambda e: crawl_site(websites[e.pk]), batch, workers
        ):
            try:
                save_crawl(enrichment, outcome)
            except DatabaseError as exc:
                # Un dato raro de una web no debe tumbar el lote entero. La instancia ya lleva
                # los datos que han fallado: se guarda solo el error, con un UPDATE aparte.
                logger.warning("No se pudo guardar el rastreo de %s: %s", enrichment.company, exc)
                fields = {
                    "crawl_status": Enrichment.CrawlStatus.UNREACHABLE,
                    "crawl_error": f"Error al guardar: {exc}"[:300],
                    "crawled_at": timezone.now(),
                }
                for name, value in fields.items():
                    setattr(enrichment, name, value)
                try:
                    Enrichment.objects.filter(pk=enrichment.pk).update(**fields)
                except DatabaseError:
                    logger.exception("Tampoco se pudo guardar el error de %s", enrichment.company)
            stats.crawled += 1
            stats.crawl_status[enrichment.crawl_status] += 1


# --- Extracción ----------------------------------------------------------------------------------


def extraction_input(company: Company, pages: list[CompanyPage]) -> str:
    order = {kind: i for i, kind in enumerate(CompanyPage.Kind.values)}
    parts = [
        f"Empresa: {company.name}",
        f"Categoría en nuestro directorio: {company.category.name if company.category else '—'}",
        f"Web: {company.website}",
        "",
    ]
    for page in sorted(pages, key=lambda p: order.get(p.kind, 99)):
        budget = PAGE_CHAR_BUDGET.get(page.kind, DEFAULT_PAGE_CHARS)
        parts.append(
            f'<pagina tipo="{page.kind}" url="{page.url}">\n{page.text[:budget]}\n</pagina>'
        )
    return "\n".join(parts)


def _extract_one(item: tuple[Enrichment, str]):
    _, user_text = item
    version, system = load_prompt(EXTRACT_PROMPT)
    return call_structured(
        purpose=LLMCall.Purpose.ENRICHMENT,
        prompt_version=version,
        system=system,
        user_text=user_text,
        output_model=ExtractedCompany,
        fast=True,
        max_tokens=4096,  # 2048 se quedaba corto con webs de muchos servicios o clientes
    )


def apply_extraction(enrichment: Enrichment, result, page_urls: set[str]) -> None:
    data: ExtractedCompany = result.output
    enrichment.is_company_site = data.is_company_site
    enrichment.summary = data.summary
    enrichment.services = data.services
    enrichment.clients = data.clients
    enrichment.size_estimate = data.size_estimate
    enrichment.requires_catalan = data.requires_catalan
    enrichment.site_languages = list(dict.fromkeys(data.site_languages))
    # Solo URLs que hemos visto de verdad: el modelo no puede inventarse una página de empleo.
    enrichment.jobs_url = data.jobs_url if data.jobs_url in page_urls else ""
    enrichment.hiring_note = data.hiring_note
    enrichment.extracted_at = timezone.now()
    enrichment.extracted_pages_hash = enrichment.pages_hash
    enrichment.extraction_model = result.call.model
    enrichment.extraction_prompt_version = result.call.prompt_version
    enrichment.extraction_attempts = 0
    enrichment.error = ""
    enrichment.save()


def extract(stats: EnrichStats, deadline: Deadline, workers: int, limit: int | None = None) -> None:
    pending = [
        e
        for e in Enrichment.objects.select_related("company", "company__category").filter(
            company__is_active=True, crawl_status=Enrichment.CrawlStatus.OK
        )
        if e.needs_extraction
    ][:limit]
    pages: dict[int, list[CompanyPage]] = {}
    for page in CompanyPage.objects.filter(company_id__in=[e.company_id for e in pending]):
        pages.setdefault(page.company_id, []).append(page)

    consecutive_errors = 0
    chunk = max(workers, 1) * 2
    for start in range(0, len(pending), chunk):
        if deadline.passed:
            stats.notes.append(
                "Tiempo agotado durante el análisis; se sigue en la próxima ejecución."
            )
            return
        batch = [
            (e, extraction_input(e.company, pages.get(e.company_id, [])))
            for e in pending[start : start + chunk]
        ]
        for (enrichment, _), outcome in run_concurrently(_extract_one, batch, workers):
            if isinstance(outcome, LLMNotConfigured):
                stats.notes.append(f"IA no configurada: {outcome}")
                return
            if isinstance(outcome, Exception):
                consecutive_errors += 1
                stats.llm_errors += 1
                message = str(outcome) if isinstance(outcome, LLMError) else repr(outcome)
                # Tras MAX_EXTRACTION_ATTEMPTS fallos deja de reintentarse y se puntúa sin ella.
                Enrichment.objects.filter(pk=enrichment.pk).update(
                    error=message[:300], extraction_attempts=F("extraction_attempts") + 1
                )
                logger.warning("Extracción de %s fallida: %s", enrichment.company, message)
                continue
            consecutive_errors = 0
            urls = {p.url for p in pages.get(enrichment.company_id, [])}
            apply_extraction(enrichment, outcome, urls)
            stats.extracted += 1
        if consecutive_errors >= MAX_CONSECUTIVE_LLM_ERRORS:
            stats.notes.append(
                "Demasiados errores seguidos de la IA; se reintentará en la próxima ejecución."
            )
            return


# --- Puntuación ----------------------------------------------------------------------------------


def company_card(enrichment: Enrichment) -> dict:
    """Lo que el modelo sabe de una empresa al puntuarla (compacto, sin texto de páginas)."""
    company = enrichment.company
    card = {
        "company_id": company.pk,
        "nombre": company.name,
        "categoria": company.category.name if company.category else "",
        "zona": company.zone.name if company.zone else "",
        "web": enrichment.get_crawl_status_display(),
    }
    if enrichment.extracted_at:
        card |= {
            "resumen": enrichment.summary,
            "servicios": enrichment.services,
            "clientes": enrichment.clients,
            "tamano": enrichment.get_size_estimate_display(),
            "requiere_catalan": enrichment.requires_catalan,
            "idiomas_web": enrichment.site_languages,
            "contratacion": enrichment.hiring_note,
            "web_es_de_la_empresa": enrichment.is_company_site,
        }
    return card


def select_for_scoring(fingerprint: str) -> list[Enrichment]:
    """Sin puntuar, puntuadas con otro perfil o con datos extraídos más nuevos que la nota.

    Las webs rastreadas pero aún sin analizar esperan a la extracción (salvo que esta
    haya fallado ya `MAX_EXTRACTION_ATTEMPTS` veces: ver `Enrichment.needs_extraction`).
    """
    qs = (
        Enrichment.objects.select_related("company", "company__category", "company__zone")
        .filter(company__is_active=True)
        .exclude(crawl_status=Enrichment.CrawlStatus.PENDING)
        .filter(
            Q(scored_at__isnull=True)
            | ~Q(profile_hash=fingerprint)
            | Q(extracted_at__gt=F("scored_at"))
        )
        .order_by("-company__confidence_score", "company_id")
    )
    return [e for e in qs if not e.needs_extraction]


def _score_batch(item: tuple[list[Enrichment], str, str]):
    enrichments, brief, _ = item
    version, system = load_prompt(SCORE_PROMPT)
    companies = "\n".join(
        json.dumps(company_card(e), ensure_ascii=False, sort_keys=True) for e in enrichments
    )
    return call_structured(
        purpose=LLMCall.Purpose.SCORING,
        prompt_version=version,
        system=system,
        user_text=f"<perfil>\n{brief}\n</perfil>\n\n<empresas>\n{companies}\n</empresas>",
        output_model=ScoreBatch,
        fast=True,
        max_tokens=4096,
    )


def apply_scores(enrichments: list[Enrichment], result, fingerprint: str) -> int:
    by_id = {e.company_id: e for e in enrichments}
    now = timezone.now()
    updated = []
    for score in result.output.scores:
        enrichment = by_id.pop(score.company_id, None)
        if enrichment is None:  # id inventado o repetido
            continue
        enrichment.fit_score = score.fit_score
        enrichment.fit_breakdown = score.breakdown
        enrichment.fit_reason = score.reason
        enrichment.hook = score.hook
        enrichment.scored_at = now
        enrichment.profile_hash = fingerprint
        enrichment.scoring_model = result.call.model
        enrichment.scoring_prompt_version = result.call.prompt_version
        enrichment.error = ""
        updated.append(enrichment)
    Enrichment.objects.bulk_update(
        updated,
        [
            "fit_score",
            "fit_breakdown",
            "fit_reason",
            "hook",
            "scored_at",
            "profile_hash",
            "scoring_model",
            "scoring_prompt_version",
            "error",
        ],
    )
    return len(updated)


def score(profile: Profile | None, stats: EnrichStats, deadline: Deadline, workers: int) -> None:
    if not profile_is_usable(profile):
        stats.notes.append("Sin puntuar: el perfil está vacío (sube y analiza el CV).")
        return
    brief = profile_brief(profile)
    fingerprint = profile_fingerprint(profile)
    pending = select_for_scoring(fingerprint)
    batches = [
        (pending[i : i + SCORE_BATCH_SIZE], brief, fingerprint)
        for i in range(0, len(pending), SCORE_BATCH_SIZE)
    ]
    consecutive_errors = 0
    chunk = max(workers, 1) * 2
    for start in range(0, len(batches), chunk):
        if deadline.passed:
            stats.notes.append(
                "Tiempo agotado durante la puntuación; se sigue en la próxima ejecución."
            )
            return
        for (enrichments, _, _), outcome in run_concurrently(
            _score_batch, batches[start : start + chunk], workers
        ):
            if isinstance(outcome, LLMNotConfigured):
                stats.notes.append(f"IA no configurada: {outcome}")
                return
            if isinstance(outcome, Exception):
                consecutive_errors += 1
                stats.llm_errors += 1
                logger.warning("Puntuación de un lote fallida: %s", outcome)
                continue
            applied = apply_scores(enrichments, outcome, fingerprint)
            if not applied:
                # Ningún id del lote: si se quedara en la caché, el mismo lote recibiría la
                # misma respuesta inútil cada noche. Se borra y cuenta como error.
                outcome.call.delete()
                consecutive_errors += 1
                stats.llm_errors += 1
                logger.warning("Lote de puntuación sin ningún id válido; se reintentará")
                continue
            consecutive_errors = 0
            stats.scored += applied
        if consecutive_errors >= MAX_CONSECUTIVE_LLM_ERRORS:
            stats.notes.append(
                "Demasiados errores seguidos de la IA; se reintentará en la próxima ejecución."
            )
            return


# --- Orquestación --------------------------------------------------------------------------------


def run_enrichment(
    *,
    mode: str = "all",
    limit: int = 150,
    max_minutes: float | None = 40,
    crawl_workers: int = CRAWL_WORKERS,
    llm_workers: int = LLM_WORKERS,
) -> EnrichStats:
    """`all`: rastrear (hasta `limit` webs) + analizar + puntuar. `score`: solo puntuar."""
    stats = EnrichStats()
    deadline = Deadline(max_minutes)
    profile = current_profile()
    ensure_enrichments()
    if mode == "all":
        crawl(select_for_crawl(limit, profile), stats, deadline, crawl_workers)
        extract(stats, deadline, llm_workers)
    score(profile, stats, deadline, llm_workers)
    return stats
