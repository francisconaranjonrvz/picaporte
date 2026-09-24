"""Casos de uso de las rutas: crearla, editarla a mano, enlazar con Google Maps y marcar paradas."""

from datetime import date
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Zone
from apps.companies.dedupe import distance_m
from apps.companies.models import Company
from apps.tracking import services as tracking
from apps.tracking.models import Note, Visit

from .models import Route, RouteStop
from .planner import MAX_STOPS, Candidate, order_by_proximity, plan, zone_center

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


# --- Edición a mano -------------------------------------------------------------------------


class RouteError(Exception):
    """Cambio no permitido en una ruta (llena, parada ya resuelta...)."""


def current_route() -> Route | None:
    """La ruta en la que se añaden empresas: la última, si no es de un día pasado."""
    route = Route.objects.select_related("zone").first()
    if route is None or route.date < timezone.localdate():
        return None
    return route


def origin_of(route: Route) -> tuple[float, float]:
    if route.start_lat is not None and route.start_lng is not None:
        return (route.start_lat, route.start_lng)
    return zone_center(route.zone)


def _coords(stop: RouteStop) -> tuple[float, float]:
    return (stop.company.lat, stop.company.lng)


def _save_order(route: Route, stops: list[RouteStop]) -> None:
    """Numera las paradas en este orden y recalcula la distancia desde la anterior."""
    previous = origin_of(route)
    for position, stop in enumerate(stops, start=1):
        stop.position = position
        stop.distance_m = round(distance_m(*previous, *_coords(stop)))
        previous = _coords(stop)
    RouteStop.objects.bulk_update(stops, ["position", "distance_m"])


def _ordered(route: Route) -> list[RouteStop]:
    return list(route.stops.select_related("company").order_by("position", "pk"))


@transaction.atomic
def add_stop(route: Route, company: Company) -> RouteStop:
    """Añade la empresa donde menos alarga el paseo (inserción más barata)."""
    if company.lat is None or company.lng is None:
        raise RouteError("Esta empresa no tiene ubicación en el mapa.")
    existing = route.stops.filter(company=company).first()
    if existing is not None:
        return existing
    stops = _ordered(route)
    if len(stops) >= MAX_STOPS:
        raise RouteError(f"La ruta ya tiene {MAX_STOPS} paradas: quita alguna antes.")
    point = (company.lat, company.lng)
    path = [origin_of(route), *[_coords(s) for s in stops]]
    # Solo entre paradas pendientes: lo ya visitado se queda donde estaba.
    first_free = next((i for i, s in enumerate(stops) if not s.is_done), len(stops))
    best, best_cost = len(stops), None
    for index in range(first_free, len(stops) + 1):
        before = path[index]
        after = path[index + 1] if index + 1 < len(path) else None
        cost = distance_m(*before, *point)
        if after is not None:
            cost += distance_m(*point, *after) - distance_m(*before, *after)
        if best_cost is None or cost < best_cost:
            best, best_cost = index, cost
    stop = RouteStop(route=route, company=company, position=0)
    stop.save()
    stops.insert(best, stop)
    _save_order(route, stops)
    return stop


def add_to_current_route(company: Company) -> tuple[Route, RouteStop]:
    """Desde el mapa o la ficha: a la ruta en curso o, si no hay, a una nueva para hoy."""
    route = current_route()
    if route is None:
        route = Route.objects.create(date=timezone.localdate(), slot=Route.Slot.DAY)
    return route, add_stop(route, company)


@transaction.atomic
def remove_stop(stop: RouteStop) -> None:
    if stop.is_done:
        raise RouteError("Esa parada ya está marcada: desmárcala antes de quitarla.")
    route = stop.route
    stop.delete()
    _save_order(route, _ordered(route))


@transaction.atomic
def reorder(route: Route, stop_ids: list[int]) -> None:
    """Orden elegido a mano (arrastrando). Las paradas que falten en la lista van al final."""
    stops = _ordered(route)
    by_id = {s.pk: s for s in stops}
    ordered = [by_id.pop(pk) for pk in dict.fromkeys(stop_ids) if pk in by_id]
    _save_order(route, ordered + [s for s in stops if s.pk in by_id])


@transaction.atomic
def optimize(route: Route) -> None:
    """Reordena por cercanía las pendientes, saliendo de la última ya resuelta."""
    stops = _ordered(route)
    done = [s for s in stops if s.is_done]
    pending = [s for s in stops if not s.is_done]
    start = _coords(done[-1]) if done else origin_of(route)
    candidates = [Candidate(s.company, 0) for s in pending]
    by_company = {s.company_id: s for s in pending}
    ordered = [by_company[c.company.pk] for c in order_by_proximity(candidates, start)]
    _save_order(route, done + ordered)


# --- Google Maps ------------------------------------------------------------------------------


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
        # Solo se restaura si nadie ha cambiado el estado después (ficha u otra ruta).
        applied = STATE_TO_VISIT.get(stop.state)
        if (
            stop.is_done
            and stop.previous_status
            and applied is not None
            and tracking.visit_for(stop.company).status == applied
        ):
            tracking.set_status(stop.company, stop.previous_status)
        stop.previous_status = ""
    elif not stop.is_done:
        stop.previous_status = tracking.visit_for(stop.company).status
    if state != RouteStop.State.CLOSED and stop.closed_note_id:
        stop.closed_note.delete()
        stop.closed_note = None
    elif state == RouteStop.State.CLOSED and stop.closed_note_id is None:
        stop.closed_note = Note.objects.create(
            company=stop.company, text=f"Cerrada al pasar en la ruta del {stop.route.date:%d/%m}."
        )
    stop.state = state
    stop.save(update_fields=["state", "previous_status", "closed_note", "updated_at"])
    status = STATE_TO_VISIT.get(state)
    if status is not None:
        tracking.set_status(stop.company, status)
    return stop
