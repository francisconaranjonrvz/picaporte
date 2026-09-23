"""Pestaña de ofertas: importar el export de career-ops (o CSV/JSON) y ver el cruce."""

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from . import services
from .importers import MAX_BYTES, ImportFormatError, parse
from .models import JobOffer, active_offers


class ImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo",
        help_text="scan-history.tsv de career-ops, o un CSV/JSON con título, empresa y url (máx. 2 MB).",
        widget=forms.ClearableFileInput(
            attrs={"accept": ".tsv,.csv,.json,text/csv,application/json"}
        ),
    )


FILTERS = {
    "": ("Activas", lambda qs: active_offers()),
    "empresa": ("En tus empresas", lambda qs: active_offers().filter(company__isnull=False)),
    "todas": ("Todas", lambda qs: qs),
}


@require_GET
def ofertas(request):
    key = request.GET.get("ver", "")
    key = key if key in FILTERS else ""
    qs = FILTERS[key][1](JobOffer.objects.all()).select_related("company")
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    context = {
        "title": "Ofertas",
        "form": ImportForm(),
        "page": page,
        "filters": [(k, label) for k, (label, _) in FILTERS.items()],
        "current": key,
        "counts": {
            "total": JobOffer.objects.count(),
            "active": active_offers().count(),
            "matched": active_offers().filter(company__isnull=False).count(),
        },
    }
    return render(request, "offers/ofertas.html", context)


@require_POST
def importar(request):
    form = ImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Elige un archivo para importar.")
        return redirect("ofertas")
    upload = form.cleaned_data["file"]
    if upload.size > MAX_BYTES:
        messages.error(request, "El archivo supera 2 MB.")
        return redirect("ofertas")
    try:
        result = parse(upload.name, upload.read())
    except ImportFormatError as exc:
        messages.error(request, str(exc))
        return redirect("ofertas")
    stats = services.import_offers(result)
    messages.success(
        request,
        f"{stats.total} ofertas ({stats.created} nuevas) · {stats.matched} cruzadas con "
        f"{len(stats.matched_companies)} empresas · {stats.skipped} descartadas.",
    )
    return redirect("ofertas")


@require_POST
def recruzar(request):
    matched = services.rematch_all()
    messages.success(request, f"Cruce actualizado: {matched} ofertas con empresa.")
    return redirect("ofertas")
