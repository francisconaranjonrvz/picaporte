"""Desarrollo local: DEBUG, SQLite y estáticos sin manifest."""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import env

# Vercel ejecuta manage.py en el build: si llega aquí es que falta la variable.
if env("VERCEL", default="") or env("VERCEL_ENV", default=""):
    raise ImproperlyConfigured(
        "Define DJANGO_SETTINGS_MODULE=config.settings.production en las variables de Vercel."
    )

DEBUG = True
SECRET_KEY = env("SECRET_KEY", default="django-insecure-solo-desarrollo-local")
ALLOWED_HOSTS = ["*"]

STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
