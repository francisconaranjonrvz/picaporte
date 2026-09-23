from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from apps.tracking.models import Visit

from .design import PAIRS, TOKENS

# Estados de visita del modelo real, con su badge (se muestran en /styleguide).
VISIT_STATES = [(value, label, Visit.BADGES[value]) for value, label in Visit.Status.choices]


@login_not_required
@require_GET
def health(request):
    """Comprobación de vida: proceso + conexión a la base de datos."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        db = "ok"
    except DatabaseError:
        db = "error"
    payload = {
        "status": "ok" if db == "ok" else "degraded",
        "db": db,
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
    }
    return JsonResponse(payload, status=200 if db == "ok" else 503)


@login_not_required
def styleguide(request):
    """Catálogo vivo del design system (público: no expone datos)."""
    context = {
        "title": "Styleguide",
        "tokens": TOKENS,
        "pairs": PAIRS,
        "visit_states": VISIT_STATES,
    }
    return render(request, "core/styleguide.html", context)


@login_not_required
def styleguide_demo(request):
    """Parcial cargado por HTMX desde /styleguide para demostrar skeleton + swap."""
    return render(request, "core/styleguide_demo.html")


@login_not_required
@require_GET
def manifest(request):
    return render(request, "pwa/manifest.webmanifest", content_type="application/manifest+json")


@login_not_required
@never_cache
@require_GET
def service_worker(request):
    return render(request, "pwa/sw.js", content_type="application/javascript")


@login_not_required
@require_GET
def offline(request):
    return render(request, "core/offline.html", {"title": "Sin conexión"})
