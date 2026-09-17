import pytest

from django.db import connections
from django.db.utils import OperationalError
from django.urls import reverse


@pytest.mark.django_db
def test_health_es_publico_y_toca_la_bd(client):
    resp = client.get(reverse("health"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert set(body) == {"status", "db", "version", "env"}


@pytest.mark.django_db
def test_health_devuelve_503_si_la_bd_falla(client, monkeypatch):
    def cursor_roto(*args, **kwargs):
        raise OperationalError("neon caído")

    monkeypatch.setattr(connections["default"], "cursor", cursor_roto)

    resp = client.get(reverse("health"))

    assert resp.status_code == 503
    assert resp.json()["db"] == "error"
    assert resp.json()["status"] == "degraded"


def test_health_solo_admite_get(client):
    assert client.post(reverse("health")).status_code == 405
