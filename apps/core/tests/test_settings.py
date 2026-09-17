"""Importa los módulos de settings con un entorno controlado (sin reconfigurar Django)."""

import importlib
import sys

import pytest

from django.core.exceptions import ImproperlyConfigured

PROD_ENV = {
    "SECRET_KEY": "x" * 64,
    "DATABASE_URL": "postgres://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/neondb",
    "VERCEL_URL": "picaporte-abc123.vercel.app",
    "VERCEL_PROJECT_PRODUCTION_URL": "picaporte.vercel.app",
    "ALLOWED_HOSTS": "picaporte.example.com",
}


def fresh_import(module: str, monkeypatch, env: dict[str, str]):
    # Aísla el test del .env del desarrollador.
    monkeypatch.setattr("environ.Env.read_env", lambda *args, **kwargs: None)
    for key in (
        "VERCEL",
        "VERCEL_ENV",
        "VERCEL_URL",
        "VERCEL_BRANCH_URL",
        "ALLOWED_HOSTS",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for name in ("config.settings.base", module):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(module)


def test_production_es_seguro_y_deriva_hosts_de_vercel(monkeypatch):
    prod = fresh_import("config.settings.production", monkeypatch, PROD_ENV)

    assert prod.DEBUG is False
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert prod.ALLOWED_HOSTS == [
        "picaporte.example.com",
        ".vercel.app",
        "picaporte-abc123.vercel.app",
        "picaporte.vercel.app",
    ]
    assert "https://picaporte-abc123.vercel.app" in prod.CSRF_TRUSTED_ORIGINS
    assert "https://*.vercel.app" in prod.CSRF_TRUSTED_ORIGINS


def test_production_endurece_la_conexion_a_neon(monkeypatch):
    prod = fresh_import("config.settings.production", monkeypatch, PROD_ENV)
    db = prod.DATABASES["default"]

    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert db["CONN_MAX_AGE"] == 0
    assert db["CONN_HEALTH_CHECKS"] is True
    assert db["DISABLE_SERVER_SIDE_CURSORS"] is True
    assert db["OPTIONS"]["sslmode"] == "require"
    assert db["OPTIONS"]["connect_timeout"] == 10


def test_production_respeta_sslmode_de_la_url(monkeypatch):
    env = {**PROD_ENV, "DATABASE_URL": "postgres://u:p@localhost:5432/db?sslmode=disable"}
    prod = fresh_import("config.settings.production", monkeypatch, env)

    assert prod.DATABASES["default"]["OPTIONS"]["sslmode"] == "disable"


def test_production_exige_secret_key(monkeypatch):
    env = {k: v for k, v in PROD_ENV.items() if k != "SECRET_KEY"}
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ImproperlyConfigured):
        fresh_import("config.settings.production", monkeypatch, env)


def test_local_rechaza_ejecutarse_en_vercel(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match=r"config\.settings\.production"):
        fresh_import("config.settings.local", monkeypatch, {"VERCEL": "1"})


def test_local_usa_sqlite_y_debug(monkeypatch):
    local = fresh_import("config.settings.local", monkeypatch, {})

    assert local.DEBUG is True
    assert local.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
