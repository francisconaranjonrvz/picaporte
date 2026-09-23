import json
from datetime import date, time
from urllib.parse import parse_qs, urlsplit

import pytest

from django.urls import reverse

from apps.catalog.models import Zone
from apps.companies.models import Company
from apps.enrichment.models import Enrichment
from apps.routes import planner, services
from apps.routes.models import Route, RouteStop
from apps.tracking.models import Favorite, Note, Visit

TUESDAY = date(2026, 9, 22)
MORNING = (time(9, 30), time(14, 0))


def _company(name, lat, lng, score=None, zone="eixample", hours="", **kw):
    company = Company.objects.create(
        name=name,
        lat=lat,
        lng=lng,
        zone=Zone.objects.get(slug=zone) if zone else None,
        opening_hours=hours,
        **kw,
    )
    if score is not None:
        Enrichment.objects.create(company=company, crawl_status="ok", fit_score=score)
    return company


@pytest.fixture
def eixample(db):
    return Zone.objects.get(slug="eixample")


def test_prioridad_favoritas_encaje_y_estado(db):
    base = _company("Base", 41.39, 2.16, score=50)
    fav = _company("Fav", 41.39, 2.16, score=50)
    Favorite.objects.create(company=fav, priority=Favorite.Priority.HIGH)
    volver = _company("Volver", 41.39, 2.16, score=50)
    Visit.objects.create(company=volver, status=Visit.Status.RETURN, next_action_on=TUESDAY)

    p = {
        c.name: planner.priority_for(c, TUESDAY)
        for c in Company.objects.select_related("enrichment", "visit", "favorite")
    }
    assert p["Fav"] - p["Base"] == 45  # favorita de prioridad alta
    assert p["Volver"] - p["Base"] == 20 + 25  # estado "volver" + próxima acción ese día
    assert planner.priority_for(_company("Sin nota", 41.39, 2.16), TUESDAY) == pytest.approx(
        0.6 * 30
    )
    assert base  # la base no tiene bonus


def test_candidatas_filtran_zona_horario_y_resueltas(eixample):
    _company("Abierta", 41.39, 2.16, hours="Mo-Fr 09:00-18:00")
    _company("Solo tardes", 41.39, 2.16, hours="Mo-Fr 16:00-20:00")
    _company("Sin horario", 41.39, 2.16)  # horario de oficina estimado
    _company("Otra zona", 41.40, 2.20, zone="poblenou")
    _company("Sin coordenadas", None, None)
    entregada = _company("Entregada", 41.39, 2.16)
    Visit.objects.create(company=entregada, status=Visit.Status.CV_DELIVERED)

    names = {c.company.name for c in planner.candidates(TUESDAY, MORNING, eixample)}
    assert names == {"Abierta", "Sin horario"}
    sunday = date(2026, 9, 27)
    assert planner.candidates(sunday, MORNING, eixample) == []


def test_orden_por_cercania_mejora_el_recorrido(db):
    # Cuatro puntos en línea (oeste -> este) desordenados; salida al oeste.
    points = [(41.39, 2.150), (41.39, 2.170), (41.39, 2.155), (41.39, 2.165)]
    stops = [
        planner.Candidate(Company(name=str(i), lat=lat, lng=lng), 1)
        for i, (lat, lng) in enumerate(points)
    ]
    ordered = planner.order_by_proximity(stops, (41.39, 2.14))
    assert [c.company.lng for c in ordered] == [2.150, 2.155, 2.165, 2.170]


def test_plan_limita_paradas_y_elige_las_prioritarias(eixample):
    for i in range(14):
        _company(f"E{i:02d}", 41.385 + i * 0.001, 2.16, score=40 + i)
    stops, origin = planner.plan(TUESDAY, MORNING, eixample, size=25)
    assert len(stops) == planner.MAX_STOPS  # 25 se recorta a 10
    assert {c.company.name for c in stops} == {f"E{i:02d}" for i in range(4, 14)}
    assert origin == planner.zone_center(eixample)
    stops, _ = planner.plan(TUESDAY, MORNING, eixample, size=1)
    assert len(stops) == planner.MIN_STOPS


def test_crear_ruta_calcula_distancias_sin_tocar_el_seguimiento(eixample):
    for i in range(7):
        _company(f"E{i}", 41.385 + i * 0.002, 2.16, score=60)

    route = services.create_route(TUESDAY, Route.Slot.MORNING, eixample, 6, start=(41.385, 2.16))
    stops = list(route.stops.select_related("company"))
    assert len(stops) == 6
    assert [s.position for s in stops] == [1, 2, 3, 4, 5, 6]
    assert stops[0].distance_m < 50  # la primera está junto al punto de salida
    assert all(s.distance_m < 300 for s in stops)  # paradas a ~220 m unas de otras
    assert not Visit.objects.exists()  # proponer una ruta no cambia estados


def _stops(n):
    route = Route.objects.create(date=TUESDAY)
    return [
        RouteStop.objects.create(
            route=route,
            company=_company(f"S{i}", 41.39 + i / 1000, 2.16 + i / 1000),
            position=i + 1,
        )
        for i in range(n)
    ]


def test_enlaces_de_google_maps(db):
    stops = _stops(10)
    url = services.maps_url(stops)
    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://www.google.com/maps/dir/?")
    assert query["travelmode"] == ["walking"]
    assert "origin" not in query  # sale de la ubicación actual
    assert query["destination"] == ["41.399000,2.169000"]
    assert len(query["waypoints"][0].split("|")) == 9

    legs = services.maps_legs(stops)
    assert [(a, b) for a, b, _ in legs] == [(1, 4), (5, 8), (9, 10)]
    second = parse_qs(urlsplit(legs[1][2]).query)
    assert second["origin"] == ["41.393000,2.163000"]  # sale de la parada 4
    assert len(second["waypoints"][0].split("|")) == 3
    assert services.maps_url([]) == ""


def test_marcar_paradas_actualiza_el_seguimiento_y_deshacer_lo_restaura(db):
    stop = _stops(1)[0]
    Visit.objects.create(company=stop.company, status=Visit.Status.RETURN)

    services.mark_stop(stop, RouteStop.State.DELIVERED)
    assert Visit.objects.get(company=stop.company).status == Visit.Status.CV_DELIVERED
    services.mark_stop(stop, RouteStop.State.VISITED)  # cambiar de marca no pierde el original
    services.mark_stop(stop, RouteStop.State.PENDING)
    assert Visit.objects.get(company=stop.company).status == Visit.Status.RETURN

    services.mark_stop(stop, RouteStop.State.CLOSED)
    assert Visit.objects.get(company=stop.company).status == Visit.Status.RETURN
    assert Note.objects.filter(company=stop.company, text__startswith="Cerrada al pasar").exists()

    services.mark_stop(stop, RouteStop.State.SKIPPED)
    assert Visit.objects.get(company=stop.company).status == Visit.Status.RETURN


def test_pagina_ruta_crear_y_modo(auth_client, eixample):
    resp = auth_client.get(reverse("ruta"))
    assert resp.status_code == 200
    assert "Proponer ruta" in resp.text

    for i in range(6):
        _company(f"Agencia {i}", 41.385 + i * 0.002, 2.16, score=70)
    resp = auth_client.post(
        reverse("ruta_crear"),
        {"date": "2026-09-22", "slot": "manana", "zone": eixample.pk, "size": 6},
    )
    assert resp.status_code == 302
    route = Route.objects.get()

    html = auth_client.get(reverse("ruta")).text
    assert "6 paradas" in html
    assert "google.com/maps/dir" in html
    assert "Paradas 1-4" in html  # tramos para navegador móvil
    assert reverse("modo_ruta", args=[route.pk]) in html

    html = auth_client.get(reverse("modo_ruta", args=[route.pk])).text
    assert "0 de 6 paradas" in html
    stop = route.stops.first()
    resp = auth_client.post(reverse("parada_estado", args=[stop.pk]), {"state": "entregado"})
    assert resp.status_code == 200
    assert "Deshacer" in resp.text
    assert json.loads(resp["HX-Trigger"])["route-progress"] is True
    assert "1 de 6 paradas" in auth_client.get(reverse("ruta_progreso", args=[route.pk])).text
    assert (
        auth_client.post(reverse("parada_estado", args=[stop.pk]), {"state": "x"}).status_code
        == 400
    )


def test_formulario_invalido_y_sin_candidatas(auth_client, eixample):
    resp = auth_client.post(reverse("ruta_crear"), {"date": "", "slot": "manana", "size": 6})
    assert resp.status_code == 400
    assert "field-error" in resp.text

    resp = auth_client.post(
        reverse("ruta_crear"), {"date": "2026-09-27", "slot": "manana", "size": 6}
    )  # domingo: nada abierto
    assert resp.status_code == 302
    assert "No hay empresas para esa franja" in auth_client.get(reverse("ruta")).text


def test_ubicacion_fuera_de_barcelona_se_rechaza(auth_client, db):
    resp = auth_client.post(
        reverse("ruta_crear"),
        {"date": "2026-09-22", "slot": "manana", "size": 6, "start_lat": 40.4, "start_lng": -3.7},
    )
    assert resp.status_code == 400


def test_los_campos_de_fecha_usan_formato_iso(auth_client, db):
    """<input type="date"> solo acepta AAAA-MM-DD: con el formato local saldría vacío."""
    from django.utils import timezone

    html = auth_client.get(reverse("ruta")).text
    assert f'value="{timezone.localdate().isoformat()}"' in html

    company = _company("Sol", 41.39, 2.16)
    Visit.objects.create(company=company, next_action_on=date(2026, 10, 1))
    html = auth_client.get(reverse("ficha", args=[company.pk])).text
    assert 'value="2026-10-01"' in html
