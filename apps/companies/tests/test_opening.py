from datetime import datetime, time

import pytest

from apps.companies.opening import DEFAULT_OFFICE_HOURS, opening_for, parse

MON = datetime(2026, 9, 21, 10, 0)  # lunes


def at(day_offset: int, hh: int, mm: int = 0) -> datetime:
    return MON.replace(day=21 + day_offset, hour=hh, minute=mm)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("24/7", {d: [(0, 1440)] for d in range(7)}),
        ("Mo-Fr 09:00-18:00", {d: [(540, 1080)] for d in range(5)}),
        (
            "Mo-Th 09:00-14:00,15:00-18:00; Fr 09:00-15:00",
            {**{d: [(540, 840), (900, 1080)] for d in range(4)}, 4: [(540, 900)]},
        ),
        ("Mo,We 10:00-12:00", {0: [(600, 720)], 2: [(600, 720)]}),
        ("Sa-Mo 10:00-13:00", {5: [(600, 780)], 6: [(600, 780)], 0: [(600, 780)]}),
        ("10:00-20:00", {d: [(600, 1200)] for d in range(7)}),
        # Una regla posterior sustituye a la anterior para sus días.
        (
            "Mo-Fr 09:00-18:00; We off",
            {0: [(540, 1080)], 1: [(540, 1080)], 2: [], 3: [(540, 1080)], 4: [(540, 1080)]},
        ),
        ("Mo-Fr 09:00-18:00; PH off", {d: [(540, 1080)] for d in range(5)}),
        ("", None),
        ("por la mañana", None),
        ("PH off", None),
    ],
)
def test_parse(value, expected):
    assert parse(value) == expected


WEEKDAYS_9_18 = {d: [(540, 1080)] for d in range(5)}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Días separados solo por espacios: antes lanzaba ValueError (error 500).
        ("Mo Tu We Th Fr 09:00-18:00", WEEKDAYS_9_18),
        ("Mo-Fr Sa 10:00-14:00", {d: [(600, 840)] for d in range(6)}),
        ("Sa Su off", {5: [], 6: []}),
        # La jornada de verano (meses) se ignora en vez de pisar toda la semana.
        (
            "Mo-Th 09:00-14:00,15:00-18:00; Fr 09:00-15:00; Jul-Aug Mo-Fr 08:00-15:00",
            {**{d: [(540, 840), (900, 1080)] for d in range(4)}, 4: [(540, 900)]},
        ),
        ("Mo-Fr 09:00-18:00; SH off", WEEKDAYS_9_18),
        # `, ` delante de otros días separa reglas; delante de una franja, no.
        (
            "Mo-Fr 08:00-20:00, Sa 09:00-14:00",
            {**{d: [(480, 1200)] for d in range(5)}, 5: [(540, 840)]},
        ),
        ("Mo-Fr 09:00-14:00, 16:00-19:00", {d: [(540, 840), (960, 1140)] for d in range(5)}),
        # Los festivos se quitan de la lista de días.
        ("24/7; PH off", {d: [(0, 1440)] for d in range(7)}),
        ("Mo-Su 09:00-21:00; Su,PH off", {**{d: [(540, 1260)] for d in range(6)}, 6: []}),
        ("Mo[1] 10:00-12:00", None),
    ],
)
def test_parse_formatos_de_osm_menos_habituales(value, expected):
    assert parse(value) == expected


def test_horario_ilegible_usa_el_estimado(monkeypatch):
    def broken(value):
        if value != DEFAULT_OFFICE_HOURS:
            raise ValueError("formato raro")
        return parse(value)

    monkeypatch.setattr("apps.companies.opening.parse", broken)
    opening = opening_for("Mo Tu 09:00-18:00")
    assert opening.estimated
    assert opening.is_open(at(1, 11))


def test_abierto_ahora_y_horario_de_hoy():
    opening = opening_for("Mo-Fr 09:00-14:00,16:00-19:00")
    assert not opening.estimated
    assert opening.is_open(at(0, 10))
    assert not opening.is_open(at(0, 15))
    assert not opening.is_open(at(5, 10))  # sábado
    assert opening.today_label(at(0, 10)) == "Hoy 09:00-14:00, 16:00-19:00"
    assert opening.today_label(at(6, 10)) == "Cerrado hoy"


def test_franja_que_cruza_la_medianoche():
    opening = opening_for("Fr 22:00-03:00")
    assert opening.is_open(at(4, 23))
    assert opening.is_open(at(4, 1))


def test_sin_horario_usa_el_de_oficina_estimado():
    opening = opening_for("")
    assert opening.estimated
    assert opening.schedule == parse(DEFAULT_OFFICE_HOURS)
    assert opening.is_open(at(1, 11))
    assert not opening.is_open(at(1, 14, 30))  # hora de comer


def test_abre_en_una_franja():
    opening = opening_for("Mo-Fr 09:30-14:00")
    assert opening.is_open_between(0, time(13, 0), time(15, 0))
    assert not opening.is_open_between(0, time(14, 0), time(16, 0))
    assert not opening.is_open_between(6, time(10, 0), time(12, 0))
