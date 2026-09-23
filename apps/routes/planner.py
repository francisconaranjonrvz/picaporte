"""Planificador de rutas a pie: qué empresas visitar y en qué orden.

1. Candidatas: activas, con coordenadas, en la zona, abiertas en la franja de ese
   día (horario OSM o de oficina estimado) y que no estén ya resueltas (CV
   entregado, visitada o descartada).
2. Prioridad: favorita (según su prioridad), encaje con el perfil, estado "volver"
   o "planificado", y próxima acción programada para ese día.
3. Se eligen las `size` más prioritarias y se ordenan por cercanía: vecino más
   cercano desde el punto de salida y mejora 2-opt (con ≤ 10 paradas es inmediato).
"""

from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from apps.catalog.models import Zone
from apps.companies.dedupe import distance_m
from apps.companies.models import Company
from apps.companies.opening import opening_for
from apps.tracking.models import Favorite, Visit

BARCELONA_CENTER = (41.3874, 2.1686)  # plaça de Catalunya
MIN_STOPS, MAX_STOPS, DEFAULT_STOPS = 6, 10, 8
DONE_STATUSES = {Visit.Status.CV_DELIVERED, Visit.Status.VISITED, Visit.Status.DISCARDED}
FAVORITE_BONUS = {
    Favorite.Priority.HIGH: 45,
    Favorite.Priority.MEDIUM: 30,
    Favorite.Priority.LOW: 15,
}
STATUS_BONUS = {Visit.Status.RETURN: 20, Visit.Status.PLANNED: 10}
UNSCORED_FIT = 30  # encaje supuesto para empresas aún sin puntuar
WALKING_M_PER_MIN = 75  # a paso tranquilo, con semáforos


@dataclass
class Candidate:
    company: Company
    priority: float

    @property
    def point(self) -> tuple[float, float]:
        return (self.company.lat, self.company.lng)


def zone_center(zone: Zone | None) -> tuple[float, float]:
    if zone is not None and len(zone.bbox) == 4:
        lng_min, lat_min, lng_max, lat_max = zone.bbox
        return ((lat_min + lat_max) / 2, (lng_min + lng_max) / 2)
    return BARCELONA_CENTER


def priority_for(company: Company, day: date) -> float:
    score = 0.0
    favorite = getattr(company, "favorite", None)
    if favorite is not None:
        score += FAVORITE_BONUS.get(favorite.priority, 20)
    enrichment = getattr(company, "enrichment", None)
    fit = enrichment.fit_score if enrichment and enrichment.fit_score is not None else UNSCORED_FIT
    score += 0.6 * fit
    visit = getattr(company, "visit", None)
    if visit is not None:
        score += STATUS_BONUS.get(visit.status, 0)
        if visit.next_action_on == day:
            score += 25
    return score + company.confidence_score / 20


def candidates(day: date, slot_times, zone: Zone | None) -> list[Candidate]:
    start, end = slot_times
    qs = (
        Company.objects.filter(is_active=True, lat__isnull=False, lng__isnull=False)
        .select_related("enrichment", "visit", "favorite", "category")
        .exclude(visit__status__in=DONE_STATUSES)
    )
    if zone is not None:
        qs = qs.filter(zone=zone)
    weekday = day.weekday()
    return [
        Candidate(company, priority_for(company, day))
        for company in qs
        if opening_for(company.opening_hours).is_open_between(weekday, start, end)
    ]


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(distance_m(*a, *b) for a, b in pairwise(points))


def order_by_proximity(stops: list[Candidate], start: tuple[float, float]) -> list[Candidate]:
    """Vecino más cercano desde `start` y después 2-opt hasta que no mejore."""
    pending = list(stops)
    ordered: list[Candidate] = []
    here = start
    while pending:
        nearest = min(pending, key=lambda c: distance_m(*here, *c.point))
        ordered.append(nearest)
        pending.remove(nearest)
        here = nearest.point

    improved = True
    while improved:
        improved = False
        for i in range(len(ordered) - 1):
            for j in range(i + 2, len(ordered) + 1):
                candidate = ordered[:i] + ordered[i:j][::-1] + ordered[j:]
                if _path_length([start, *[c.point for c in candidate]]) + 1e-6 < _path_length(
                    [start, *[c.point for c in ordered]]
                ):
                    ordered, improved = candidate, True
    return ordered


def plan(
    day: date,
    slot_times,
    zone: Zone | None,
    size: int = DEFAULT_STOPS,
    start: tuple[float, float] | None = None,
) -> tuple[list[Candidate], tuple[float, float]]:
    """(paradas ordenadas, punto de salida). Puede devolver menos de `size` si no hay más."""
    size = max(MIN_STOPS, min(MAX_STOPS, size))
    origin = start or zone_center(zone)
    pool = sorted(
        candidates(day, slot_times, zone),
        key=lambda c: (-c.priority, c.company.name),
    )[:size]
    return order_by_proximity(pool, origin), origin


def walking_minutes(meters: float) -> int:
    return max(1, round(meters * 1.25 / WALKING_M_PER_MIN))  # 1,25: las calles no son rectas
