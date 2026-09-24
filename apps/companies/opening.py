"""Horarios: intérprete del subconjunto habitual de `opening_hours` de OSM.

Admite `24/7`, reglas separadas por `;` (o por `, ` antes de otros días) con
días (`Mo-Fr`, `Mo,We`, `Mo Tu`, `Sa`) y franjas (`09:00-14:00,16:00-19:00`),
`off`/`closed` y reglas sin días (todos). Como en OSM, una regla posterior
sustituye a las anteriores para sus días. Lo que no se entiende se ignora: los
festivos `PH` se quitan de la lista de días y las reglas con meses, semanas o
vacaciones (`Jul-Aug …`, `SH off`) se saltan enteras. Si no queda nada
utilizable (o el texto no se puede interpretar) se usa el horario estimado.

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
_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_ITEM = rf"(?:{_DAY}(?:\s*-\s*{_DAY})?|PH)\b"
# Selector de días al principio de la regla: `Mo-Fr`, `Mo,We`, `Mo Tu`, `Su,PH`…
_SELECTOR = re.compile(rf"^{_ITEM}(?:\s*,?\s*{_ITEM})*")
_DAY_RANGE = re.compile(rf"({_DAY})(?:\s*-\s*({_DAY}))?")
# Reglas que dependen del calendario (meses, semanas, vacaciones escolares, fechas).
_CALENDAR = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|week|SH|easter)\b|\[")
# `;` separa reglas; `, ` también cuando viene tras una franja u `off` y antecede a otros días.
_RULES = re.compile(rf";|(?:(?<=\d)|(?<=off)|(?<=closed)),\s*(?=(?:{_DAY}|PH)\b)")


def _days(spec: str) -> list[int]:
    days: list[int] = []
    for start, end in _DAY_RANGE.findall(spec):
        first = DAYS.index(start)
        last = DAYS.index(end) if end else first
        span = range(first, last + 1) if first <= last else [*range(first, 7), *range(last + 1)]
        days.extend(span)
    return days


def _minutes(hours: str, minutes: str) -> int:
    return int(hours) * 60 + int(minutes)


def parse(value: str | None) -> Schedule | None:
    value = (value or "").strip()
    if not value:
        return None
    schedule: Schedule = {}
    understood = False
    for rule in (r.strip() for r in _RULES.split(value)):
        if not rule or _CALENDAR.search(rule):
            continue
        match = _SELECTOR.match(rule)
        if match:
            days = _days(match.group(0))
            if not days:  # solo festivos (`PH off`)
                continue
            rest = rule[match.end() :].strip(" ,")
        else:
            days, rest = list(range(7)), rule
            if re.search(rf"\b{_DAY}\b", rest):  # días detrás de algo que no entendemos
                continue
        if rest.lower() in {"off", "closed"}:
            for day in days:
                schedule[day] = []
            understood = True
            continue
        if rest == "24/7":
            ranges = [(0, 24 * 60)]
        else:
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
    try:
        schedule = parse(opening_hours)
    except ValueError:  # texto de OSM que no sabemos leer: mejor el estimado que un error 500
        schedule = None
    if schedule is not None:
        return Opening(schedule, estimated=False)
    return Opening(parse(DEFAULT_OFFICE_HOURS), estimated=True)
