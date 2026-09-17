import pytest

from django.urls import reverse


def test_anonimo_redirige_a_login(client, db):
    resp = client.get("/")

    assert resp.status_code == 302
    assert resp.url == f"{reverse('login')}?next=/"


def test_login_renderiza_sin_navegacion(client, db):
    resp = client.get(reverse("login"))

    assert resp.status_code == 200
    assert 'class="tabbar"' not in resp.text
    assert 'autocomplete="current-password"' in resp.text


def test_login_correcto_redirige_a_explorar(client, user):
    resp = client.post(reverse("login"), {"username": "laura", "password": "secreta-123"})

    assert resp.status_code == 302
    assert resp.url == reverse("explorar")
    assert client.get(reverse("explorar")).status_code == 200


def test_login_incorrecto_no_autentica(client, user):
    resp = client.post(reverse("login"), {"username": "laura", "password": "mal"})

    assert resp.status_code == 200
    assert not resp.wsgi_request.user.is_authenticated
    assert 'role="alert"' in resp.text


def test_login_autenticado_redirige(auth_client):
    resp = auth_client.get(reverse("login"))

    assert resp.status_code == 302
    assert resp.url == reverse("explorar")


def test_logout_solo_por_post(auth_client):
    assert auth_client.get(reverse("logout")).status_code == 405

    resp = auth_client.post(reverse("logout"))

    assert resp.status_code == 302
    assert resp.url == reverse("login")
    assert auth_client.get(reverse("explorar")).status_code == 302


@pytest.mark.django_db
def test_admin_login_es_accesible_sin_sesion(client):
    assert client.get("/admin/login/").status_code == 200
