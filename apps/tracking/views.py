"""Acciones de un toque (HTMX) y pestaña Favoritas. Nada de esto contacta con las empresas."""

import json

from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.companies.models import Company

from . import services
from .forms import VisitForm
from .models import Favorite, Visit


def _company(pk) -> Company:
    return get_object_or_404(Company, pk=pk)


def _toast(response: HttpResponse, message: str) -> HttpResponse:
    response["HX-Trigger"] = json.dumps({"toast": message})
    return response


@require_POST
def favorite_toggle(request, pk):
    company = _company(pk)
    favorite = services.toggle_favorite(company)
    response = render(
        request, "tracking/_heart.html", {"company": company, "is_favorite": favorite is not None}
    )
    return _toast(response, "Añadida a favoritas" if favorite else "Quitada de favoritas")


@require_POST
def status_update(request, pk):
    company = _company(pk)
    status = request.POST.get("status", "")
    if status not in Visit.Status.values:
        return HttpResponseBadRequest("Estado desconocido")
    visit = services.set_status(company, status)
    response = render(
        request,
        "tracking/_status_panel.html",
        {"company": company, "visit": visit, "statuses": Visit.Status.choices},
    )
    response["HX-Trigger"] = json.dumps(
        {"toast": f"Estado: {visit.get_status_display()}", "notes-changed": True}
    )
    return response


@require_POST
def visit_update(request, pk):
    company = _company(pk)
    visit = services.visit_for(company)
    form = VisitForm(request.POST, instance=visit)
    if form.is_valid():
        form.save()
        return _toast(
            render(
                request,
                "tracking/_visit_form.html",
                {"company": company, "form": VisitForm(instance=visit)},
            ),
            "Seguimiento guardado",
        )
    return render(
        request, "tracking/_visit_form.html", {"company": company, "form": form}, status=400
    )


@require_POST
def note_add(request, pk):
    company = _company(pk)
    services.add_note(company, request.POST.get("text", ""))
    return render(
        request, "tracking/_notes.html", {"company": company, "notes": company.notes.all()[:50]}
    )


@require_GET
def notes(request, pk):
    company = _company(pk)
    return render(
        request, "tracking/_notes.html", {"company": company, "notes": company.notes.all()[:50]}
    )


@require_GET
def favoritas(request):
    favorites = list(
        Favorite.objects.select_related(
            "company", "company__category", "company__zone", "company__enrichment", "company__visit"
        )
    )
    today = timezone.localdate()
    upcoming = (
        Visit.objects.filter(next_action_on__isnull=False, company__favorite__isnull=False)
        .exclude(status=Visit.Status.DISCARDED)
        .select_related("company")
        .order_by("next_action_on")[:5]
    )
    return render(
        request,
        "tracking/favoritas.html",
        {
            "title": "Favoritas",
            "favorites": favorites,
            "priorities": Favorite.Priority.choices,
            "upcoming": upcoming,
            "today": today,
        },
    )


@require_POST
def favorites_reorder(request):
    """Recibe el orden tras arrastrar y soltar: `ids=3,1,2`."""
    try:
        ids = [int(x) for x in request.POST.get("ids", "").split(",") if x]
    except ValueError:
        return HttpResponseBadRequest("ids no válidos")
    services.reorder_favorites(ids)
    return HttpResponse(status=204)


@require_POST
def favorite_update(request, pk):
    favorite = get_object_or_404(Favorite, company_id=pk)
    priority = request.POST.get("priority")
    if priority is not None:
        if priority not in Favorite.Priority.values:
            return HttpResponseBadRequest("Prioridad desconocida")
        favorite.priority = priority
    if "note" in request.POST:
        favorite.note = request.POST["note"].strip()[:280]
    favorite.save(update_fields=["priority", "note"])
    return _toast(HttpResponse(status=204), "Guardado")
