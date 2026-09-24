"""Pestañas Explorar y Mapa, ficha de empresa y panel de estado de los datos."""

from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.enrichment.models import Enrichment, FitScore
from apps.enrichment.profile import profile_for, profile_is_usable, stale_scores_count
from apps.jobs.models import JobRun
from apps.jobs.views import latest
from apps.offers.models import active_offers
from apps.tracking import services as tracking
from apps.tracking.forms import VisitForm
from apps.tracking.models import Visit

from .filters import CompanyFilter, base_queryset
from .models import Company, Source, SourceRecord
from .opening import opening_for
from .personal import attach, for_user

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
    form = CompanyFilter(request.GET or None, user=request.user)
    query = request.GET.copy()
    query.pop("after", None)
    context = {"title": "Explorar", "form": form, "query": query.urlencode()}
    if request.htmx and "after" in request.GET:
        try:
            anchor = for_user(
                Company.objects.filter(pk=int(request.GET["after"])), request.user
            ).first()
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
    profile = profile_for(request.user)
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
            "scored": FitScore.objects.filter(user=request.user, company__is_active=True).count(),
            "stale": stale_scores_count(profile),
            "profile_ready": profile_is_usable(profile),
        },
    }
    return render(request, "companies/datos.html", context)


@require_GET
def ficha(request, pk):
    company = attach(
        get_object_or_404(Company.objects.select_related("category", "zone", "enrichment"), pk=pk),
        request.user,
    )
    now = timezone.localtime()
    opening = opening_for(company.opening_hours)
    enrichment = getattr(company, "enrichment", None)
    context = {
        "in_route": company.pk in _route_company_ids(request.user),
        "title": company.name,
        "company": company,
        "enrichment": enrichment,
        "score": company.score,
        "visit": company.visit,
        "favorite": company.favorite,
        "opening": opening,
        "open_now": opening.is_open(now),
        "today_label": opening.today_label(now),
        "notes": tracking.notes_for(request.user, company),
        "offers": active_offers(request.user).filter(company=company)[:10],
        "sources": [Source(s).label for s in dict.fromkeys(company.source_names)],
        "statuses": Visit.Status.choices,
        "form": VisitForm(instance=company.visit),
    }
    return render(request, "companies/ficha.html", context)


@require_GET
def mapa(request):
    form = CompanyFilter(request.GET or None, user=request.user)
    return render(request, "companies/mapa.html", {"title": "Mapa", "form": form})


@require_GET
def mapa_datos(request):
    """Puntos del mapa con los mismos filtros que Explorar (JSON compacto)."""
    form = CompanyFilter(request.GET or None, user=request.user)
    results = form.apply(base_queryset(request.user).filter(lat__isnull=False, lng__isnull=False))
    points = []
    in_route = _route_company_ids(request.user)
    for company in list(results)[:MAP_LIMIT]:
        visit = company.visit
        points.append(
            {
                "id": company.pk,
                "name": company.name,
                "lat": round(company.lat, 6),
                "lng": round(company.lng, 6),
                "score": company.fit,
                "category": company.category.name if company.category else "",
                "status": visit.get_status_display() if visit else "",
                "favorite": company.is_favorite,
                "in_route": company.pk in in_route,
                "url": company_url(company),
            }
        )
    return JsonResponse({"points": points, "truncated": len(points) == MAP_LIMIT})


def _route_company_ids(user) -> set[int]:
    """Empresas de la ruta en curso del usuario (para el botón "Añadir a la ruta")."""
    from apps.routes.services import current_route  # routes depende de companies

    route = current_route(user)
    return set(route.stops.values_list("company_id", flat=True)) if route else set()


def company_url(company: Company) -> str:
    from django.urls import reverse

    return reverse("ficha", args=[company.pk])
