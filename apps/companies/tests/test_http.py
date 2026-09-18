from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from django.utils import timezone

from apps.companies import http as chttp
from apps.companies.models import FetchCache


class FakeClient:
    """Sustituto de httpx.Client: cola de respuestas y registro de peticiones."""

    calls = []
    outcomes = []

    def __init__(self, headers=None, timeout=None):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, data=None):
        FakeClient.calls.append((method, url, data, self.headers))
        outcome = FakeClient.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(status_code=outcome[0], text=outcome[1])


@pytest.fixture
def fake_http(monkeypatch):
    FakeClient.calls = []
    FakeClient.outcomes = [(200, '{"ok": true}')]
    monkeypatch.setattr(chttp.httpx, "Client", FakeClient)
    return FakeClient


@pytest.mark.django_db
def test_cachea_respuestas_2xx_y_envia_user_agent_propio(fake_http):
    first = chttp.fetch("https://api.example/x", method="POST", data={"data": "q"})
    second = chttp.fetch("https://api.example/x", method="POST", data={"data": "q"})

    assert first.cached is False
    assert second.cached is True
    assert second.json() == {"ok": True}
    assert len(fake_http.calls) == 1
    assert fake_http.calls[0][3]["User-Agent"].startswith("picaporte/")
    assert "github.com/francisconaranjonrvz/picaporte" in fake_http.calls[0][3]["User-Agent"]
    assert FetchCache.objects.count() == 1


@pytest.mark.django_db
def test_la_clave_incluye_metodo_url_y_cuerpo(fake_http):
    fake_http.outcomes = [(200, "a"), (200, "b"), (200, "c")]

    chttp.fetch("https://api.example/x", data={"data": "q1"})
    chttp.fetch("https://api.example/x", data={"data": "q2"})
    chttp.fetch("https://api.example/x", method="POST", data={"data": "q1"})

    assert len(fake_http.calls) == 3
    assert FetchCache.objects.count() == 3


@pytest.mark.django_db
def test_no_cachea_errores_y_respeta_ttl_y_force(fake_http):
    fake_http.outcomes = [(504, "timeout"), (200, "ok"), (200, "ok2"), (200, "ok3")]

    assert chttp.fetch("https://api.example/e").status_code == 504
    assert FetchCache.objects.count() == 0

    chttp.fetch("https://api.example/e")
    FetchCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))  # caducada
    assert chttp.fetch("https://api.example/e").text == "ok2"
    assert chttp.fetch("https://api.example/e", force=True).text == "ok3"
    assert len(fake_http.calls) == 4


@pytest.mark.django_db
def test_errores_de_red_se_traducen(fake_http):
    fake_http.outcomes = [httpx.ConnectError("boom")]

    with pytest.raises(chttp.FetchError, match="boom"):
        chttp.fetch("https://api.example/x")
