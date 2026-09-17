import pytest

from django.urls import reverse

TABS = ["explorar", "mapa", "favoritas", "ruta", "perfil"]


@pytest.mark.parametrize("url_name", TABS)
def test_pestanas_requieren_login(client, db, url_name):
    resp = client.get(reverse(url_name))

    assert resp.status_code == 302
    assert resp.url.startswith(f"{reverse('login')}?next=")


@pytest.mark.parametrize("url_name", TABS)
def test_pestanas_renderizan_autenticado(auth_client, url_name):
    resp = auth_client.get(reverse(url_name))

    assert resp.status_code == 200
    assert "Picaporte" in resp.text


def test_bottom_nav_tiene_cinco_pestanas_y_marca_la_activa(auth_client):
    resp = auth_client.get(reverse("mapa"))

    for url_name in TABS:
        assert f'href="{reverse(url_name)}"' in resp.text
    assert resp.text.count('class="tab"') == 5
    assert resp.text.count('aria-current="page"') == 1
    assert f'href="{reverse("mapa")}" aria-current="page"' in resp.text


def test_base_envia_csrf_a_htmx_y_no_usa_boost(auth_client):
    resp = auth_client.get(reverse("explorar"))

    assert 'hx-headers=\'{"x-csrftoken": "' in resp.text
    assert "hx-boost" not in resp.text


def test_styleguide_es_publico_y_muestra_contraste(client, db):
    resp = client.get(reverse("styleguide"))

    assert resp.status_code == 200
    assert "PASS" in resp.text
    assert "FAIL" not in resp.text
    # Público pero sin sesión: no debe mostrar la navegación de la app.
    assert 'class="tabbar"' not in resp.text


def test_styleguide_demo_devuelve_parcial(client, db):
    resp = client.get(reverse("styleguide_demo"))

    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert "Agencia Ejemplo" in resp.text


def test_perfil_muestra_version_y_formulario_de_salida(auth_client, settings):
    settings.APP_VERSION = "abc1234"

    resp = auth_client.get(reverse("perfil"))

    assert "abc1234" in resp.text
    assert f'<form method="post" action="{reverse("logout")}"' in resp.text
