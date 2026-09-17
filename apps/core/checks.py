"""System checks propios de Picaporte."""

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def debug_outside_local(app_configs, **kwargs):
    """W001: DEBUG=True en un entorno que no es el local (p. ej. Vercel)."""
    if settings.DEBUG and getattr(settings, "APP_ENV", "local") != "local":
        return [
            Warning(
                "DEBUG está activado fuera del entorno local.",
                hint="Usa config.settings.production (DJANGO_SETTINGS_MODULE) en Vercel.",
                id="picaporte.W001",
            )
        ]
    return []
