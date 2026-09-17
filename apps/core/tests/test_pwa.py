import struct

import pytest

from django.conf import settings
from django.urls import reverse

ICONS = [
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("icon-512-maskable.png", 512),
    ("apple-touch-icon-180.png", 180),
]


def test_manifest_es_publico_y_completo(client, db):
    resp = client.get(reverse("manifest"))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/manifest+json"
    manifest = resp.json()
    assert manifest["id"] == "/"
    assert manifest["name"] == "Picaporte"
    assert manifest["short_name"] == "Picaporte"
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["lang"] == "es"
    assert manifest["theme_color"] == "#F5F9FF"
    assert manifest["background_color"] == "#F5F9FF"
    sizes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert sizes == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}


def test_service_worker_es_js_sin_cache(client, db, settings):
    settings.APP_VERSION = "f00ba7"

    resp = client.get(reverse("sw"))

    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/javascript"
    assert "no-cache" in resp["Cache-Control"]
    assert 'const CACHE = "picaporte-f00ba7";' in resp.text
    assert 'event.request.mode !== "navigate"' in resp.text


def test_pagina_offline_es_publica_y_autonoma(client, db):
    resp = client.get(reverse("offline"))

    assert resp.status_code == 200
    assert "Sin conexión" in resp.text
    # Sin estáticos: debe renderizar aunque no haya red para cargar CSS.
    assert "/static/" not in resp.text


def test_base_enlaza_manifest_e_icono_apple(auth_client):
    resp = auth_client.get(reverse("explorar"))

    assert f'<link rel="manifest" href="{reverse("manifest")}">' in resp.text
    assert 'rel="apple-touch-icon"' in resp.text
    assert "serviceWorker.register" in resp.text


@pytest.mark.parametrize(("filename", "size"), ICONS)
def test_iconos_png_existen_con_el_tamano_correcto(filename, size):
    path = settings.BASE_DIR / "static" / "icons" / filename
    header = path.read_bytes()[:24]

    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", header[16:24])  # chunk IHDR
    assert (width, height) == (size, size)
