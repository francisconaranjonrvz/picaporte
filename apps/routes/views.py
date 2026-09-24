"""Pestaña Ruta: planificar el día y "modo ruta" para ir marcando paradas."""

import json

from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.companies.models import Company

from . import services
from .forms import RouteForm
from .models import Route, RouteStop
from .planner import MAX_STOPS, walking_minutes


def _stops(route: Route) -> list[RouteStop]:
    return list(route.stops.select_related("company", "company__category", "company__enrichment"))


def _route_context(route: Route | None) -> dict:
    if route is None:
        return {"route": None}
    stops = _stops(route)
    total_m = sum(s.distance_m for s in stops)
    # Un solo enlace admite 9 paradas intermedias; con más, se abre por tramos.
    fits_one_link = len(stops) <= services.MAX_WAYPOINTS + 1
    return {
        "route": route,
        "stops": stops,
        "maps_url": services.maps_url(stops) if fits_one_link else "",
        "maps_legs": services.maps_legs(stops) if len(stops) > services.LEG_WAYPOINTS + 1 else [],
        "max_stops": MAX_STOPS,
        "total_km": round(total_m / 1000, 1),
        "total_min": walking_minutes(total_m) if total_m else 0,
        "done": sum(1 for s in stops if s.is_done),
        "points": json.dumps(
            [
                {
                    "lat": s.company.lat,
                    "lng": s.company.lng,
                    "n": s.position,
                    "name": s.company.name,
                }
                for s in stops
            ]
        ),
    }


@require_GET
def ruta(request):
    route = Route.objects.select_related("zone").first()
    context = {"title": "Ruta", "form": RouteForm(), **_route_context(route)}
    for stop in context.get("stops", []):
        stop.walk_min = walking_minutes(stop.distance_m) if stop.distance_m else 0
    return render(request, "routes/ruta.html", context)


@require_POST
def ruta_crear(request):
    form = RouteForm(request.POST)
    if not form.is_valid():
        route = Route.objects.first()
        context = {"title": "Ruta", "form": form, **_route_context(route)}
        return render(request, "routes/ruta.html", context, status=400)
    data = form.cleaned_data
    services.create_route(data["date"], data["slot"], data["zone"], data["size"], form.start)
    if form.location_ignored:
        messages.info(request, "Tu ubicación está fuera de Barcelona: salgo del centro de la zona.")
    return redirect("ruta")


@require_GET
def modo_ruta(request, pk):
    route = get_object_or_404(Route.objects.select_related("zone"), pk=pk)
    context = {"title": "Modo ruta", "states": RouteStop.State, **_route_context(route)}
    return render(request, "routes/modo.html", context)


@require_POST
def parada_estado(request, pk):
    stop = get_object_or_404(RouteStop.objects.select_related("company", "route"), pk=pk)
    state = request.POST.get("state", "")
    if state not in RouteStop.State.values:
        return HttpResponseBadRequest("Estado desconocido")
    services.mark_stop(stop, state)
    response = render(request, "routes/_stop.html", {"stop": stop, "states": RouteStop.State})
    response["HX-Trigger"] = json.dumps(
        {"toast": f"{stop.company.name}: {stop.get_state_display()}", "route-progress": True}
    )
    return response


@require_GET
def progreso(request, pk):
    route = get_object_or_404(Route, pk=pk)
    stops = list(route.stops.all())
    return render(
        request,
        "routes/_progress.html",
        {"route": route, "stops": stops, "done": sum(1 for s in stops if s.is_done)},
    )


# --- Edición a mano -----------------------------------------------------------------------------


def _back(request, fallback: str):
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect(fallback)


@require_POST
def anadir(request, company_pk):
    """Añade la empresa a la ruta en curso (o a una nueva para hoy): mapa y ficha."""
    company = get_object_or_404(Company, pk=company_pk, is_active=True)
    try:
        route, stop = services.add_to_current_route(company)
    except services.RouteError as exc:
        text, in_route = str(exc), False
    else:
        count = route.stops.count()
        text, in_route = f"{company.name}: parada {stop.position} de {count} en tu ruta.", True
    if request.htmx:
        response = render(
            request, "routes/_add_button.html", {"company": company, "in_route": in_route}
        )
        response["HX-Trigger"] = json.dumps({"toast": text})
        return response
    (messages.success if in_route else messages.error)(request, text)
    return _back(request, reverse("ficha", args=[company.pk]))


@require_POST
def quitar(request, pk):
    stop = get_object_or_404(RouteStop.objects.select_related("company", "route"), pk=pk)
    try:
        services.remove_stop(stop)
    except services.RouteError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{stop.company.name} ya no está en la ruta.")
    return redirect("ruta")


@require_POST
def ordenar(request, pk):
    """Orden arrastrado a mano (ids separados por comas) o, con `auto`, por cercanía."""
    route = get_object_or_404(Route.objects.select_related("zone"), pk=pk)
    if request.POST.get("auto"):
        services.optimize(route)
        messages.success(request, "Paradas pendientes ordenadas por cercanía.")
        return redirect("ruta")
    try:
        ids = [int(x) for x in request.POST.get("ids", "").split(",") if x.strip()]
    except ValueError:
        return HttpResponseBadRequest("ids no válidos")
    services.reorder(route, ids)
    response = redirect("ruta")
    if request.htmx:  # la distancia entre paradas cambia: se repinta la página
        response = render(request, "routes/_empty.html")
        response["HX-Refresh"] = "true"
    return response
