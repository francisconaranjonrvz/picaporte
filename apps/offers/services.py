"""Importación de ofertas y cruce con las empresas por dominio o nombre."""

from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from django.db import transaction
from django.utils import timezone

from apps.companies.dedupe import normalize_domain, normalize_name
from apps.companies.models import Company

from .importers import ParseResult
from .models import JobOffer

FUZZY_MIN = 92  # más estricto que el dedupe: aquí no hay coordenadas que lo confirmen
FUZZY_MIN_LENGTH = 5  # nombres cortos ("Sol", "BCN") dan demasiados falsos positivos
# Portales y ATS: el dominio de la oferta no dice nada de la empresa.
PORTAL_DOMAINS = (
    "greenhouse.io",
    "ashbyhq.com",
    "lever.co",
    "myworkdayjobs.com",
    "workable.com",
    "smartrecruiters.com",
    "icims.com",
    "infojobs.net",
    "indeed.com",
    "tecnoempleo.com",
    "jooble.org",
    "jobatus.es",
    "linkedin.com",
    "welcometothejungle.com",
    "jobfluent.com",
)


class CompanyMatcher:
    """Índice en memoria de las empresas activas (una consulta por importación)."""

    def __init__(self):
        companies = list(Company.objects.filter(is_active=True).only("id", "name", "domain"))
        self.by_domain = {c.domain: c for c in companies if c.domain}
        self.by_name: dict[str, Company] = {}
        for company in companies:
            self.by_name.setdefault(normalize_name(company.name), company)
        self.names = [n for n in self.by_name if len(n) >= FUZZY_MIN_LENGTH]

    def match(self, company_name: str, url: str) -> tuple[Company | None, str]:
        domain = normalize_domain(url)
        if domain and not domain.endswith(PORTAL_DOMAINS):
            for candidate in (domain, ".".join(domain.split(".")[-2:])):
                if candidate in self.by_domain:
                    return self.by_domain[candidate], JobOffer.Match.DOMAIN
        name = normalize_name(company_name)
        if name in self.by_name:
            return self.by_name[name], JobOffer.Match.NAME
        if len(name) >= FUZZY_MIN_LENGTH and self.names:
            best = process.extractOne(
                name, self.names, scorer=fuzz.token_sort_ratio, score_cutoff=FUZZY_MIN
            )
            if best is not None:
                return self.by_name[best[0]], JobOffer.Match.FUZZY
        return None, JobOffer.Match.NONE


@dataclass
class ImportStats:
    created: int = 0
    updated: int = 0
    matched: int = 0
    skipped: int = 0
    matched_companies: set[int] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.created + self.updated


@transaction.atomic
def import_offers(result: ParseResult) -> ImportStats:
    """Crea o actualiza (por URL) cada oferta y la cruza con su empresa."""
    matcher = CompanyMatcher()
    stats = ImportStats(skipped=result.skipped)
    existing = {o.url: o for o in JobOffer.objects.filter(url__in=[r.url for r in result.rows])}
    to_create, to_update = [], []
    now = timezone.now()
    for row in {r.url: r for r in result.rows}.values():  # URLs repetidas: gana la última
        company, how = matcher.match(row.company, row.url)
        offer = existing.get(row.url) or JobOffer(url=row.url)
        # Última vez vista: las ofertas sin fecha siguen activas mientras se reimporten.
        offer.imported_at = now
        offer.title = row.title
        offer.company_name = row.company
        offer.portal = row.portal
        offer.location = row.location
        offer.published_on = row.published_on
        offer.score = row.score
        offer.company = company
        offer.match = how
        offer.source = result.source
        offer.payload = row.raw
        (to_update if offer.pk else to_create).append(offer)
        if company is not None:
            stats.matched += 1
            stats.matched_companies.add(company.pk)
    JobOffer.objects.bulk_create(to_create, batch_size=500)
    JobOffer.objects.bulk_update(
        to_update,
        [
            "title",
            "company_name",
            "portal",
            "location",
            "published_on",
            "score",
            "company",
            "match",
            "source",
            "payload",
            "imported_at",
        ],
        batch_size=500,
    )
    stats.created, stats.updated = len(to_create), len(to_update)
    return stats


def rematch_all() -> int:
    """Vuelve a cruzar todas las ofertas (p. ej. tras descubrir empresas nuevas)."""
    matcher = CompanyMatcher()
    offers = list(JobOffer.objects.all())
    for offer in offers:
        offer.company, offer.match = matcher.match(offer.company_name, offer.url)
    JobOffer.objects.bulk_update(offers, ["company", "match"], batch_size=500)
    return sum(1 for o in offers if o.company_id)
