"""Pestañas Explorar y Mapa, ficha de empresa y panel de estado de los datos."""

from django.core.paginator import Paginator
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.enrichment.models import Enrichment
from apps.enrichment.profile import current_profile, profile_is_usable, stale_scores_count
from apps.jobs.models import JobRun
from apps.jobs.views import latest
from apps.tracking.forms import VisitForm
from apps.tracking.models import Visit

from .filters import CompanyFilter, base_queryset
from .models import Company, Source, SourceRecord
from .opening import opening_for

PAGE_SIZE = 20
MAP_LIMIT = 600  # marcadores como máximo (el filtro acota; Leaflet va bien con cientos)


def _with_opening(companies, now):
    for company in companies:
        company.opening = opening_for(company.opening_hours)
        company.open_now = company.opening.is_open(now)
    return companies


@require_GET
def explorar(request):
    """Lista filtrable. Con HTMX devuelve solo la página pedida (filtros o "cargar más")."""
    form = CompanyFilter(request.GET or None)
    results = form.apply()
    page = Paginator(results, PAGE_SIZE).get_page(request.GET.get("page"))
    _with_opening(page.object_list, timezone.localtime())
    query = request.GET.copy()
    query.pop("page", None)
    context = {
        "title": "Explorar",
        "form": form,
        "page": page,
        "query": query.urlencode(),
        "total_active": Company.objects.filter(is_active=True).count(),
    }
    if request.htmx and request.htmx.target in {"company-list", "load-more"}:
        template = (
            "companies/_list_page.html" if request.GET.get("page") else "companies/_list.html"
        )
        return render(request, template, context)
    return render(request, "companies/explorar.html", context)


@require_GET
def datos(request):
    """Estado de los datos: descubrimiento, análisis con IA y cifras por categoría y fuente."""
    companies = Company.objects.filter(is_active=True)
    by_category = (
        companies.values("category__name")
        .annotate(total=Count("id"))
        .order_by("-total", "category__name")
    )
    enrichments = Enrichment.objects.filter(company__is_active=True)
    profile = current_profile()
    context = {
        "title": "Datos",
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
    }
    return render(request, "companies/datos.html", context)


@require_GET
def ficha(request, pk):
    company = get_object_or_404(
        Company.objects.select_related("category", "zone", "enrichment", "visit", "favorite"), pk=pk
    )
    now = timezone.localtime()
    opening = opening_for(company.opening_hours)
    enrichment = getattr(company, "enrichment", None)
    context = {
        "title": company.name,
        "company": company,
        "enrichment": enrichment,
        "visit": getattr(company, "visit", None),
        "favorite": getattr(company, "favorite", None),
        "opening": opening,
        "open_now": opening.is_open(now),
        "today_label": opening.today_label(now),
        "notes": company.notes.all()[:50],
        "sources": [Source(s).label for s in dict.fromkeys(company.source_names)],
        "statuses": Visit.Status.choices,
        "form": VisitForm(instance=getattr(company, "visit", None)),
    }
    return render(request, "companies/ficha.html", context)


@require_GET
def mapa(request):
    form = CompanyFilter(request.GET or None)
    return render(request, "companies/mapa.html", {"title": "Mapa", "form": form})


@require_GET
def mapa_datos(request):
    """Puntos del mapa con los mismos filtros que Explorar (JSON compacto)."""
    form = CompanyFilter(request.GET or None)
    results = form.apply(base_queryset().filter(lat__isnull=False, lng__isnull=False))
    points = []
    for company in list(results)[:MAP_LIMIT]:
        enrichment = getattr(company, "enrichment", None)
        visit = getattr(company, "visit", None)
        points.append(
            {
                "id": company.pk,
                "name": company.name,
                "lat": round(company.lat, 6),
                "lng": round(company.lng, 6),
                "score": enrichment.fit_score if enrichment else None,
                "category": company.category.name if company.category else "",
                "status": visit.get_status_display() if visit else "",
                "favorite": hasattr(company, "favorite"),
                "url": company_url(company),
            }
        )
    return JsonResponse({"points": points, "truncated": len(points) == MAP_LIMIT})


def company_url(company: Company) -> str:
    from django.urls import reverse

    return reverse("ficha", args=[company.pk])
