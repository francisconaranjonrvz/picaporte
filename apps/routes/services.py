"""Casos de uso de las rutas: crear una, enlazar con Google Maps y marcar paradas."""

from datetime import date
from urllib.parse import urlencode

from django.db import transaction

from apps.catalog.models import Zone
from apps.companies.dedupe import distance_m
from apps.tracking import services as tracking
from apps.tracking.models import Note, Visit

from .models import Route, RouteStop
from .planner import plan

MAPS_DIR = "https://www.google.com/maps/dir/?"
MAX_WAYPOINTS = 9  # límite de Google Maps en la app y en escritorio
LEG_WAYPOINTS = 3  # límite en navegadores móviles

# Qué hace cada marca del modo ruta en el seguimiento de la empresa.
STATE_TO_VISIT = {
    RouteStop.State.DELIVERED: Visit.Status.CV_DELIVERED,
    RouteStop.State.VISITED: Visit.Status.VISITED,
    RouteStop.State.CLOSED: Visit.Status.RETURN,
}


@transaction.atomic
def create_route(
    day: date,
    slot: str,
    zone: Zone | None,
    size: int,
    start: tuple[float, float] | None = None,
) -> Route:
    route = Route.objects.create(
        date=day,
        slot=slot,
        zone=zone,
        start_lat=start[0] if start else None,
        start_lng=start[1] if start else None,
    )
    stops, origin = plan(day, route.times, zone, size, start)
    previous = origin
    for position, candidate in enumerate(stops, start=1):
        RouteStop.objects.create(
            route=route,
            company=candidate.company,
            position=position,
            distance_m=round(distance_m(*previous, *candidate.point)),
        )
        previous = candidate.point
    return route


def _point(stop: RouteStop) -> str:
    return f"{stop.company.lat:.6f},{stop.company.lng:.6f}"


def maps_url(stops: list[RouteStop], origin: str | None = None) -> str:
    """Direcciones a pie; sin `origin`, Google Maps sale de la ubicación actual."""
    if not stops:
        return ""
    params = {"api": "1", "travelmode": "walking", "destination": _point(stops[-1])}
    if origin:
        params["origin"] = origin
    waypoints = [_point(s) for s in stops[:-1]][:MAX_WAYPOINTS]
    if waypoints:
        params["waypoints"] = "|".join(waypoints)
    return MAPS_DIR + urlencode(params, safe="|,")


def maps_legs(stops: list[RouteStop]) -> list[tuple[int, int, str]]:
    """Tramos de ≤ 3 paradas intermedias para navegadores móviles: (desde, hasta, url)."""
    legs = []
    size = LEG_WAYPOINTS + 1
    start = 0
    while start < len(stops):
        chunk = stops[start : start + size]
        origin = _point(stops[start - 1]) if start else None
        legs.append((chunk[0].position, chunk[-1].position, maps_url(chunk, origin)))
        start += size
    return legs


@transaction.atomic
def mark_stop(stop: RouteStop, state: str) -> RouteStop:
    """Marca la parada y refleja el resultado en el seguimiento; "pendiente" deshace."""
    if state == RouteStop.State.PENDING:
        if stop.is_done and stop.previous_status:
            tracking.set_status(stop.company, stop.previous_status)
        stop.previous_status = ""
    elif not stop.is_done:
        stop.previous_status = tracking.visit_for(stop.company).status
    stop.state = state
    stop.save(update_fields=["state", "previous_status", "updated_at"])
    status = STATE_TO_VISIT.get(state)
    if status is not None:
        tracking.set_status(stop.company, status)
    if state == RouteStop.State.CLOSED:
        Note.objects.create(
            company=stop.company, text=f"Cerrada al pasar en la ruta del {stop.route.date:%d/%m}."
        )
    return stop
