from django.conf import settings


def app_meta(request):
    """Versión y entorno para plantillas (Perfil, service worker)."""
    return {"APP_VERSION": settings.APP_VERSION, "APP_ENV": settings.APP_ENV}
