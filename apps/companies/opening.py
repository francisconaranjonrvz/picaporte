"""Horarios: intérprete del subconjunto habitual de `opening_hours` de OSM.

Admite `24/7`, reglas separadas por `;` con días (`Mo-Fr`, `Mo,We`, `Sa`) y
franjas (`09:00-14:00,16:00-19:00`), `off`/`closed` y reglas sin días (todos).
Como en OSM, una regla posterior sustituye a las anteriores para sus días.
Lo que no se entiende (festivos `PH`, meses, semanas…) se ignora; si no queda
nada utilizable se devuelve None y se usa el horario de oficina estimado.

Solo OSM trae horarios: para el resto de empresas, `DEFAULT_OFFICE_HOURS` es
una estimación razonable para ir a entregar un CV (y se marca como estimada).
"""

import re
from dataclasses import dataclass
from datetime import datetime, time

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
DEFAULT_OFFICE_HOURS = "Mo-Fr 09:30-14:00,15:30-18:30"
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

Schedule = dict[int, list[tuple[int, int]]]  # día (0 = lunes) -> [(min_inicio, min_fin)]

_TIME = r"(\d{1,2}):(\d{2})"
_RANGE = re.compile(rf"{_TIME}\s*-\s*{_TIME}")
_DAYS = re.compile(r"^(?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?\s*,?\s*)+")


def _days(spec: str) -> list[int]:
    days: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, end = (DAYS.index(d) for d in part.split("-", 1))
            span = range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)]
            days.extend(span)
        else:
            days.append(DAYS.index(part))
    return days


def _minutes(hours: str, minutes: str) -> int:
    return int(hours) * 60 + int(minutes)


def parse(value: str | None) -> Schedule | None:
    value = (value or "").strip()
    if not value:
        return None
    if value == "24/7":
        return {day: [(0, 24 * 60)] for day in range(7)}
    schedule: Schedule = {}
    understood = False
    for rule in (r.strip() for r in value.split(";")):
        if not rule or rule.startswith("PH"):
            continue
        match = _DAYS.match(rule)
        days = _days(match.group(0)) if match else list(range(7))
        rest = rule[match.end() :].strip() if match else rule
        if rest.lower() in {"off", "closed"}:
            for day in days:
                schedule[day] = []
            understood = True
            continue
        ranges = [
            (_minutes(h1, m1), _minutes(h2, m2) or 24 * 60)
            for h1, m1, h2, m2 in _RANGE.findall(rest)
        ]
        if not ranges:
            continue
        for day in days:
            schedule[day] = ranges
        understood = True
    return schedule if understood else None


@dataclass(frozen=True)
class Opening:
    schedule: Schedule
    estimated: bool  # True si no hay horario real y se usa el de oficina

    def is_open(self, at: datetime) -> bool:
        minute = at.hour * 60 + at.minute
        for start, end in self.schedule.get(at.weekday(), []):
            if start <= end and start <= minute < end:
                return True
            if start > end and (minute >= start or minute < end):  # cruza la medianoche
                return True
        return False

    def is_open_between(self, day: int, start: time, end: time) -> bool:
        """¿Abre en algún momento entre `start` y `end` ese día? (para planificar rutas)."""
        lo, hi = start.hour * 60 + start.minute, end.hour * 60 + end.minute
        return any(s < hi and lo < e for s, e in self.schedule.get(day, []))

    def today_label(self, at: datetime) -> str:
        ranges = self.schedule.get(at.weekday(), [])
        if not ranges:
            return "Cerrado hoy"
        text = ", ".join(
            f"{s // 60:02d}:{s % 60:02d}-{e // 60:02d}:{e % 60:02d}" for s, e in ranges
        )
        return f"Hoy {text}"


def opening_for(opening_hours: str | None) -> Opening:
    schedule = parse(opening_hours)
    if schedule is not None:
        return Opening(schedule, estimated=False)
    return Opening(parse(DEFAULT_OFFICE_HOURS), estimated=True)
