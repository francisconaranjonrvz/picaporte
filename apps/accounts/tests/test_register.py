import pytest

from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()
DATA = {"username": "marta", "password1": "Cartel-de-2026", "password2": "Cartel-de-2026"}


@pytest.mark.django_db
def test_registro_crea_cuenta_normal_y_entra(client):
    resp = client.get(reverse("register"))
    assert resp.status_code == 200
    assert 'name="password2"' in resp.text

    resp = client.post(reverse("register"), DATA, follow=True)

    user = User.objects.get(username="marta")
    assert (user.is_staff, user.is_superuser) == (False, False)
    assert resp.redirect_chain[-1][0] == reverse("perfil")
    assert "Cuenta creada" in resp.text
    assert resp.context["user"] == user


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"password1": "123", "password2": "123"}, "field-error"),
        ({"password2": "otra-distinta-9"}, "field-error"),
        ({"username": "laura"}, "field-error"),  # ya existe
        ({"website": "http://spam.example"}, "Formulario no válido"),  # trampa para bots
    ],
)
def test_registro_rechaza_datos_invalidos(client, user, changes, error):
    resp = client.post(reverse("register"), {**DATA, **changes})
    assert resp.status_code == 200
    assert error in resp.text
    assert not User.objects.filter(username="marta").exists()


@pytest.mark.django_db
def test_registro_cerrado_al_llegar_al_tope(client, user, settings):
    settings.MAX_USERS = 1
    resp = client.post(reverse("register"), DATA)
    assert resp.status_code == 403
    assert "registro está cerrado" in resp.text
    assert User.objects.count() == 1


def test_con_sesion_iniciada_el_registro_lleva_al_perfil(auth_client):
    resp = auth_client.get(reverse("register"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("perfil")


@pytest.mark.django_db
def test_login_enlaza_al_registro(client):
    assert reverse("register") in client.get(reverse("login")).text
