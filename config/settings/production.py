"""Producción (Vercel) y trabajos de GitHub Actions contra Neon.

Solo SECRET_KEY y DATABASE_URL son obligatorias: Vercel importa este módulo
durante el build, así que nada aquí puede abrir conexiones ni depender de
variables que solo existan en runtime.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import env

DEBUG = False
SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    # GitHub Actions pasa los secretos no definidos como cadena vacía.
    raise ImproperlyConfigured("SECRET_KEY vacía: define el secreto en Vercel / GitHub.")

# Neon: en Vercel la URL *-pooler* (PgBouncer, modo transacción); en Actions la directa.
_db_url = env("DATABASE_URL", default="").strip().strip("'\"")
DATABASES = {"default": env.db_url_config(_db_url) if _db_url else {}}
if not DATABASES["default"]:
    # Diagnóstico sin revelar credenciales: solo longitud y esquema.
    _scheme = _db_url.split("://", 1)[0] if "://" in _db_url else "(sin esquema)"
    raise ImproperlyConfigured(
        f"DATABASE_URL vacía o inválida (longitud {len(_db_url)}, esquema {_scheme!r}): "
        "pega la URL completa postgresql://... de Neon (pooled en Vercel, directa en Actions)."
    )
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
