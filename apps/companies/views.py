from django.db.models import Count
from django.shortcuts import render

from apps.jobs.views import latest_discover

from .models import Company


def explorar(request):
    """Pestaña Explorar: en la fase 3 muestra el estado del descubrimiento y las cifras.

    La lista con filtros, mapa y ficha llegan en la fase 5.
    """
    companies = Company.objects.filter(is_active=True)
    by_category = (
        companies.values("category__name")
        .annotate(total=Count("id"))
        .order_by("-total", "category__name")
    )
    context = {
        "title": "Explorar",
        "total": companies.count(),
        "by_category": [
            (row["category__name"] or "Sin categoría", row["total"]) for row in by_category
        ],
        "with_website": companies.exclude(website="").count(),
        "job": latest_discover(),
    }
    return render(request, "companies/explorar.html", context)
