"""Tests: SQLite en memoria por defecto; CI inyecta Postgres vía TEST_DATABASE_URL.

Se usa una variable distinta de DATABASE_URL para que el `.env` del
desarrollador (que puede apuntar a Neon) nunca afecte a los tests.
"""

from .base import *
from .base import env

SECRET_KEY = "test-only-not-secret-0123456789abcdefghijklmnopqrstuvwxyz"
DATABASES = {"default": env.db("TEST_DATABASE_URL", default="sqlite://:memory:")}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
WHITENOISE_AUTOREFRESH = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
