from django.db.models import Count
from django.shortcuts import render

from apps.enrichment.models import Enrichment
from apps.enrichment.profile import current_profile, profile_is_usable, stale_scores_count
from apps.jobs.models import JobRun
from apps.jobs.views import latest

from .models import Company, Source, SourceRecord


def explorar(request):
    """Pestaña Explorar: estado del descubrimiento y del análisis, cifras y mejores encajes.

    La lista con filtros, mapa y ficha llegan en la fase 5.
    """
    companies = Company.objects.filter(is_active=True)
    by_category = (
        companies.values("category__name")
        .annotate(total=Count("id"))
        .order_by("-total", "category__name")
    )
    enrichments = Enrichment.objects.filter(company__is_active=True)
    profile = current_profile()
    context = {
        "title": "Explorar",
        "total": companies.count(),
        "by_category": [
            (row["category__name"] or "Sin categoría", row["total"]) for row in by_category
        ],
        "with_website": companies.exclude(website="").count(),
        "by_source": [
            (Source(row["source"]).label, row["total"])
            for row in SourceRecord.objects.filter(company__is_active=True)
            .values("source")
            .annotate(total=Count("company", distinct=True))
            .order_by("-total")
        ],
        "discover_job": latest(JobRun.Kind.DISCOVER),
        "enrich_job": latest(JobRun.Kind.ENRICH),
        "enrichment": {
            "crawled": enrichments.filter(crawl_status=Enrichment.CrawlStatus.OK).count(),
            "extracted": enrichments.filter(extracted_at__isnull=False).count(),
            "scored": enrichments.filter(fit_score__isnull=False).count(),
            "stale": stale_scores_count(profile),
            "profile_ready": profile_is_usable(profile),
        },
        "top_matches": enrichments.filter(fit_score__isnull=False)
        .select_related("company", "company__category", "company__zone")
        .order_by("-fit_score", "company__name")[:5],
    }
    return render(request, "companies/explorar.html", context)
