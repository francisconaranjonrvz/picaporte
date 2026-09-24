"""Rutas editables: añadir desde el mapa o la ficha, quitar, reordenar y más de 10 paradas."""

from datetime import timedelta

import pytest

from django.urls import reverse
from django.utils import timezone

from apps.routes import services
from apps.routes.models import Route, RouteStop
from apps.routes.planner import MAX_STOPS

from .test_routes import _company


@pytest.fixture
def route(db):
    """Ruta de hoy con tres paradas en línea de oeste a este."""
    route = Route.objects.create(date=timezone.localdate(), slot=Route.Slot.DAY)
    route.start_lat, route.start_lng = 41.39, 2.140
    route.save()
    for i, lng in enumerate((2.150, 2.160, 2.170), start=1):
        RouteStop.objects.create(
            route=route, company=_company(f"P{i}", 41.39, lng), position=i, distance_m=0
        )
    return route


def _names(route):
    return list(route.stops.order_by("position").values_list("company__name", flat=True))


def test_anadir_inserta_donde_menos_alarga_el_paseo(route):
    services.add_stop(route, _company("Entre 1 y 2", 41.39, 2.155))
    services.add_stop(route, _company("Al final", 41.39, 2.180))

    assert _names(route) == ["P1", "Entre 1 y 2", "P2", "P3", "Al final"]
    assert list(route.stops.values_list("position", flat=True)) == [1, 2, 3, 4, 5]
    first = route.stops.get(position=1)
    assert 800 < first.distance_m < 900  # desde la salida (0,01° de longitud ≈ 835 m)


def test_anadir_dos_veces_no_duplica_y_sin_coordenadas_falla(route):
    p2 = route.stops.get(position=2).company
    assert services.add_stop(route, p2).position == 2
    assert route.stops.count() == 3
    with pytest.raises(services.RouteError, match="ubicación"):
        services.add_stop(route, _company("Sin sitio", None, None))


def test_no_se_inserta_antes_de_lo_ya_visitado(route):
    RouteStop.objects.filter(position__in=(1, 2)).update(state=RouteStop.State.VISITED)
    services.add_stop(route, _company("Cerca de P1", 41.39, 2.151))
    assert _names(route)[:3] == ["P1", "P2", "Cerca de P1"]


def test_mas_de_diez_paradas_y_tope(route):
    for i in range(MAX_STOPS - 3):
        services.add_stop(route, _company(f"X{i:02d}", 41.391, 2.141 + i * 0.001))
    assert route.stops.count() == MAX_STOPS == 20
    with pytest.raises(services.RouteError, match="20 paradas"):
        services.add_stop(route, _company("Una más", 41.39, 2.2))


def test_quitar_y_reordenar_recalculan(route):
    p1, p2, p3 = route.stops.order_by("position")
    services.remove_stop(p2)
    assert _names(route) == ["P1", "P3"]
    assert route.stops.get(company__name="P3").position == 2

    services.reorder(route, [p3.pk, p1.pk, 999])
    assert _names(route) == ["P3", "P1"]
    p1.refresh_from_db()
    assert p1.distance_m > 1500  # ahora viene de P3, dos paradas más al este

    p1.state = RouteStop.State.DELIVERED
    p1.save()
    with pytest.raises(services.RouteError, match="desmárcala"):
        services.remove_stop(p1)


def test_ordenar_por_cercania_deja_lo_visitado_delante(route):
    services.reorder(route, list(route.stops.order_by("-position").values_list("pk", flat=True)))
    assert _names(route) == ["P3", "P2", "P1"]
    RouteStop.objects.filter(company__name="P3").update(state=RouteStop.State.VISITED)

    services.optimize(route)

    assert _names(route) == ["P3", "P2", "P1"]  # desde P3, lo más cerca es P2
    RouteStop.objects.update(state=RouteStop.State.PENDING)
    services.optimize(route)
    assert _names(route) == ["P1", "P2", "P3"]


def test_desde_la_ficha_se_anade_a_la_ruta_en_curso_o_a_una_nueva(auth_client):
    old = Route.objects.create(date=timezone.localdate() - timedelta(days=3))
    company = _company("Buzz", 41.39, 2.16)

    resp = auth_client.post(reverse("ruta_anadir", args=[company.pk]), follow=True)

    assert "parada 1 de 1 en tu ruta" in resp.text
    route = Route.objects.exclude(pk=old.pk).get()  # la de hace 3 días no se toca
    assert route.date == timezone.localdate()
    assert "En tu ruta" in auth_client.get(reverse("ficha", args=[company.pk])).text


def test_boton_por_htmx_y_mapa_marca_las_de_la_ruta(auth_client, route):
    company = _company("Nueva", 41.39, 2.175)
    resp = auth_client.post(reverse("ruta_anadir", args=[company.pk]), HTTP_HX_REQUEST="true")
    assert "En tu ruta" in resp.text
    assert "parada 4 de 4" in resp["HX-Trigger"]

    points = auth_client.get(reverse("mapa_datos")).json()["points"]
    in_route = {p["name"] for p in points if p["in_route"]}
    assert in_route == {"P1", "P2", "P3", "Nueva"}


def test_vistas_quitar_y_ordenar(auth_client, route):
    p1, p2, p3 = route.stops.order_by("position")
    auth_client.post(reverse("parada_quitar", args=[p2.pk]))
    assert _names(route) == ["P1", "P3"]

    resp = auth_client.post(
        reverse("ruta_ordenar", args=[route.pk]),
        {"ids": f"{p3.pk},{p1.pk}"},
        HTTP_HX_REQUEST="true",
    )
    assert resp["HX-Refresh"] == "true"
    assert _names(route) == ["P3", "P1"]

    auth_client.post(reverse("ruta_ordenar", args=[route.pk]), {"auto": "1"})
    assert _names(route) == ["P1", "P3"]
    assert (
        auth_client.post(reverse("ruta_ordenar", args=[route.pk]), {"ids": "x"}).status_code == 400
    )


def test_con_mas_de_diez_paradas_google_maps_va_por_tramos(auth_client, route):
    for i in range(9):
        services.add_stop(route, _company(f"X{i}", 41.392, 2.141 + i * 0.002))
    text = auth_client.get(reverse("ruta")).text
    assert "12 paradas" in text
    assert "Abrir en Google Maps</a>" not in text  # un enlace solo admite 10
    assert "Paradas 9-12" in text
