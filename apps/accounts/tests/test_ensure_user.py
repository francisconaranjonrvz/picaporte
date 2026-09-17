import pytest

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command


@pytest.mark.django_db
def test_crea_el_usuario_desde_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("PICAPORTE_USER", "laura")
    monkeypatch.setenv("PICAPORTE_PASSWORD", "clave-123")
    monkeypatch.setenv("PICAPORTE_EMAIL", "laura@example.com")

    call_command("ensure_user")

    user = get_user_model().objects.get(username="laura")
    assert user.check_password("clave-123")
    assert user.email == "laura@example.com"
    assert user.is_staff
    assert user.is_superuser


@pytest.mark.django_db
def test_es_idempotente_y_no_cambia_la_contrasena():
    call_command("ensure_user", username="laura", password="primera")
    call_command("ensure_user", username="laura", password="segunda")

    user = get_user_model().objects.get(username="laura")
    assert get_user_model().objects.count() == 1
    assert user.check_password("primera")


@pytest.mark.django_db
def test_reset_password_fuerza_la_nueva_contrasena():
    call_command("ensure_user", username="laura", password="primera")
    call_command("ensure_user", username="laura", password="segunda", reset_password=True)

    assert get_user_model().objects.get(username="laura").check_password("segunda")


@pytest.mark.django_db
def test_falla_si_faltan_datos(monkeypatch):
    monkeypatch.delenv("PICAPORTE_USER", raising=False)
    monkeypatch.delenv("PICAPORTE_PASSWORD", raising=False)

    with pytest.raises(CommandError, match="PICAPORTE_USER"):
        call_command("ensure_user")
