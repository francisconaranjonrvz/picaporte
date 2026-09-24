from urllib.parse import parse_qs, urlsplit

from django.urls import reverse


def test_htmx_sin_sesion_redirige_la_pagina_entera_al_login(client, db):
    """Sesión caducada: nada de pintar el login dentro de la tarjeta ni de volver a una URL POST."""
    resp = client.post(
        reverse("favorite_toggle", args=[1]),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/empresa/1/?q=sol",
    )
    assert resp.status_code == 204
    target = urlsplit(resp["HX-Redirect"])
    assert target.path == reverse("login")
    assert parse_qs(target.query) == {"next": ["/empresa/1/?q=sol"]}


def test_htmx_sin_url_actual_vuelve_a_la_portada(client, db):
    resp = client.get(reverse("job_status", args=["discover"]), HTTP_HX_REQUEST="true")
    assert resp.status_code == 204
    assert resp["HX-Redirect"] == reverse("login") + "?next=%2F"


def test_sin_htmx_el_302_de_siempre(client, db):
    resp = client.get(reverse("favoritas"))
    assert resp.status_code == 302
    assert resp["Location"].startswith(reverse("login"))


def test_con_sesion_no_cambia_nada(auth_client, db):
    resp = auth_client.get(reverse("favoritas"), HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "HX-Redirect" not in resp
