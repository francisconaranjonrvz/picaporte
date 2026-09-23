"""Pestaña Ruta: planificar el día y "modo ruta" para ir marcando paradas."""

import json

from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from . import services
from .forms import RouteForm
from .models import Route, RouteStop
from .planner import walking_minutes


def _stops(route: Route) -> list[RouteStop]:
    return list(route.stops.select_related("company", "company__category", "company__enrichment"))


def _route_context(route: Route | None) -> dict:
    if route is None:
        return {"route": None}
    stops = _stops(route)
    total_m = sum(s.distance_m for s in stops)
    return {
        "route": route,
        "stops": stops,
        "maps_url": services.maps_url(stops),
        "maps_legs": services.maps_legs(stops) if len(stops) > services.LEG_WAYPOINTS + 1 else [],
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
