from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .design import PAIRS, TOKENS

# Estados de visita (fase 5). Se muestran ya en /styleguide para fijar sus colores.
VISIT_STATES = [
    ("pendiente", "Pendiente", "badge-muted"),
    ("planificado", "Planificado", "badge-primary"),
    ("visitado", "Visitado", "badge-success"),
    ("cv_entregado", "CV entregado", "badge-success"),
    ("volver", "Volver", "badge-warning"),
    ("descartado", "Descartado", "badge-danger"),
]

# Pestañas de la barra inferior: (url_name, etiqueta, icono, fase en la que llega).
TABS = [
    ("explorar", "Explorar", "compass", 5),
    ("mapa", "Mapa", "map", 5),
    ("favoritas", "Favoritas", "heart", 5),
    ("ruta", "Ruta", "route", 6),
    ("perfil", "Perfil", "user", 2),
]


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


def _placeholder(request, tab: str):
    _, label, icon, phase = next(t for t in TABS if t[0] == tab)
    context = {
        "title": label,
        "icon": icon,
        "phase": phase,
        "text": f"Esta pestaña llega en la fase {phase}. De momento puedes explorar el design system.",
    }
    return render(request, "core/placeholder.html", context)


def mapa(request):
    return _placeholder(request, "mapa")


def favoritas(request):
    return _placeholder(request, "favoritas")


def ruta(request):
    return _placeholder(request, "ruta")


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
