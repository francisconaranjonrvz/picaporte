"""Simula el build de Vercel: collectstatic con storage manifest y render de las páginas.

`collectstatic` no renderiza plantillas, así que un `{% static %}` roto solo
explota en producción (ValueError: Missing staticfiles manifest entry).
Este test lo caza antes.
"""

import pytest

from django.core.management import call_command
from django.urls import reverse

MANIFEST_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


@pytest.fixture
def manifest_static(settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "staticfiles"
    settings.STORAGES = MANIFEST_STORAGE
    settings.WHITENOISE_AUTOREFRESH = False
    call_command("collectstatic", interactive=False, verbosity=0)
    return settings


@pytest.mark.parametrize("url_name", ["login", "styleguide", "manifest", "sw", "offline"])
def test_paginas_publicas_renderizan_con_manifest(client, db, manifest_static, url_name):
    resp = client.get(reverse(url_name))

    assert resp.status_code == 200


@pytest.mark.parametrize("url_name", ["explorar", "mapa", "favoritas", "ruta", "perfil"])
def test_pestanas_renderizan_con_manifest(auth_client, manifest_static, url_name):
    resp = auth_client.get(reverse(url_name))

    assert resp.status_code == 200
    assert "/static/css/app." in resp.text  # nombre con hash: app.<hash>.css
