"""Pestañas Explorar y Mapa, ficha de empresa y panel de estado de los datos."""

from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.enrichment.models import Enrichment
from apps.enrichment.profile import current_profile, profile_is_usable, stale_scores_count
from apps.jobs.models import JobRun
from apps.jobs.views import latest
from apps.offers.models import active_offers
from apps.tracking.forms import VisitForm
from apps.tracking.models import Visit

from .filters import CompanyFilter, base_queryset
from .models import Company, Source, SourceRecord
from .opening import opening_for

PAGE_SIZE = 20
MAP_LIMIT = 1500  # marcadores como máximo (Leaflet en canvas dibuja miles sin problema)


def _with_opening(companies, now):
    for company in companies:
        company.opening = opening_for(company.opening_hours)
        company.open_now = company.opening.is_open(now)
    return companies


def _batch(results) -> dict:
    """Una tanda de tarjetas y el cursor para la siguiente (la última empresa mostrada)."""
    companies = list(results[: PAGE_SIZE + 1])
    has_next = len(companies) > PAGE_SIZE
    companies = _with_opening(companies[:PAGE_SIZE], timezone.localtime())
    return {"companies": companies, "next_after": companies[-1].pk if has_next else None}


@require_GET
def explorar(request):
    """Lista filtrable. Con HTMX devuelve solo la lista (filtros) o la siguiente tanda.

    "Cargar más" pide `after=<pk>` (cursor) en vez de un número de página: si la lista
    cambia entre tanda y tanda (favoritas quitadas, "Abierto ahora" a una hora en punto),
    no se saltan ni se repiten tarjetas.
    """
    form = CompanyFilter(request.GET or None)
    query = request.GET.copy()
    query.pop("after", None)
    context = {"title": "Explorar", "form": form, "query": query.urlencode()}
    if request.htmx and "after" in request.GET:
        try:
            anchor = (
                Company.objects.select_related("enrichment")
                .filter(pk=int(request.GET["after"]))
                .first()
            )
        except (ValueError, OverflowError):
            anchor = None
        if anchor is None:
            return HttpResponse("")  # cursor inválido: quita el botón sin añadir tarjetas
        context.update(_batch(form.apply(after=anchor)))
        return render(request, "companies/_list_page.html", context)
    results = form.apply()
    context.update(
        _batch(results),
        total=len(results) if isinstance(results, list) else results.count(),
        total_active=Company.objects.filter(is_active=True).count(),
    )
    if request.htmx and request.htmx.target == "company-list":
        context["oob"] = True  # actualiza también el contador de filtros y "Quitar filtros"
        return render(request, "companies/_list.html", context)
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
        "offers": active_offers().filter(company=company)[:10],
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
