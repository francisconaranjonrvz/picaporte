"""Producción (Vercel) y trabajos de GitHub Actions contra Neon.

Solo SECRET_KEY y DATABASE_URL son obligatorias: Vercel importa este módulo
durante el build, así que nada aquí puede abrir conexiones ni depender de
variables que solo existan en runtime.
"""

from .base import *
from .base import env

DEBUG = False
SECRET_KEY = env("SECRET_KEY")

# Neon: en Vercel la URL *-pooler* (PgBouncer, modo transacción); en Actions la directa.
DATABASES = {"default": env.db("DATABASE_URL")}
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"].update(
        {
            "CONN_MAX_AGE": 0,  # serverless: sin conexiones persistentes
            "CONN_HEALTH_CHECKS": True,  # Neon suspende el compute a los 5 min
            "DISABLE_SERVER_SIDE_CURSORS": True,  # obligatorio con PgBouncer
        }
    )
    _options = DATABASES["default"].setdefault("OPTIONS", {})
    _options.setdefault("sslmode", "require")
    _options.setdefault("connect_timeout", 10)

# Hosts: los que inyecta Vercel (sin esquema) + comodín *.vercel.app + dominio propio.
_vercel_hosts = [
    host
    for host in (
        env("VERCEL_URL", default=""),
        env("VERCEL_BRANCH_URL", default=""),
        env("VERCEL_PROJECT_PRODUCTION_URL", default=""),
    )
    if host
]
ALLOWED_HOSTS = [*env.list("ALLOWED_HOSTS", default=[]), ".vercel.app", *_vercel_hosts]
CSRF_TRUSTED_ORIGINS = [
    *env.list("CSRF_TRUSTED_ORIGINS", default=[]),
    "https://*.vercel.app",
    *[f"https://{host}" for host in _vercel_hosts],
]

# HTTPS detrás del proxy de Vercel (reescribe x-forwarded-proto, no es falsificable).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.logs.JsonFormatter"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "django.request": {"level": "WARNING"},
        "django.security": {"level": "WARNING"},
    },
}
