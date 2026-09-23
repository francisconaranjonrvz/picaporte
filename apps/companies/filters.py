"""Filtros de la lista de Explorar y del mapa (mismos parámetros GET en ambos)."""

from django import forms
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from apps.catalog.models import Category, Zone
from apps.tracking.models import Visit

from .models import Company
from .opening import opening_for

SCORE_CHOICES = [
    ("", "Cualquier encaje"),
    ("40", "40 o más"),
    ("60", "60 o más"),
    ("80", "80 o más"),
]
CONFIDENCE_CHOICES = [
    ("", "Cualquier confianza"),
    ("55", "2+ fuentes o datos completos"),
    ("75", "Alta (75+)"),
]
CATALAN_CHOICES = [
    ("", "Catalán: indiferente"),
    ("no_requerido", "No lo exige (o no consta)"),
    ("requerido", "Lo exige"),
]
SORT_CHOICES = [("encaje", "Mejor encaje"), ("confianza", "Más confianza"), ("nombre", "Nombre")]


class CompanyFilter(forms.Form):
    q = forms.CharField(required=False, max_length=80, label="Buscar")
    score = forms.ChoiceField(required=False, choices=SCORE_CHOICES, label="Encaje")
    category = forms.ModelChoiceField(
        required=False,
        queryset=Category.objects.none(),
        empty_label="Todas las categorías",
        label="Categoría",
    )
    zone = forms.ModelChoiceField(
        required=False, queryset=Zone.objects.none(), empty_label="Todas las zonas", label="Zona"
    )
    status = forms.ChoiceField(required=False, label="Estado")
    catalan = forms.ChoiceField(required=False, choices=CATALAN_CHOICES, label="Catalán")
    confidence = forms.ChoiceField(required=False, choices=CONFIDENCE_CHOICES, label="Confianza")
    open_now = forms.BooleanField(required=False, label="Abierto ahora")
    favorites = forms.BooleanField(required=False, label="Solo favoritas")
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES, label="Orden")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["zone"].queryset = Zone.objects.filter(is_active=True)
        self.fields["status"].choices = [
            ("", "Cualquier estado"),
            ("sin_estado", "Sin empezar"),
            *Visit.Status.choices,
        ]

    @property
    def active_count(self) -> int:
        if not self.is_valid():
            return 0
        return sum(1 for k, v in self.cleaned_data.items() if v and k not in {"sort", "q"})

    def apply(self, qs: QuerySet[Company] | None = None) -> list[Company] | QuerySet[Company]:
        """Aplica los filtros. "Abierto ahora" se evalúa en Python (horarios OSM): devuelve lista."""
        qs = qs if qs is not None else base_queryset()
        if not self.is_valid():
            return qs
        data = self.cleaned_data
        if data["q"]:
            qs = qs.filter(
                Q(name__icontains=data["q"]) | Q(enrichment__services__icontains=data["q"])
            )
        if data["score"]:
            qs = qs.filter(enrichment__fit_score__gte=int(data["score"]))
        if data["category"]:
            qs = qs.filter(category=data["category"])
        if data["zone"]:
            qs = qs.filter(zone=data["zone"])
        if data["status"] == "sin_estado":
            qs = qs.filter(Q(visit__isnull=True) | Q(visit__status=Visit.Status.PENDING))
        elif data["status"]:
            qs = qs.filter(visit__status=data["status"])
        if data["catalan"] == "requerido":
            qs = qs.filter(enrichment__requires_catalan="si")
        elif data["catalan"] == "no_requerido":
            qs = qs.exclude(enrichment__requires_catalan="si")
        if data["confidence"]:
            qs = qs.filter(confidence_score__gte=int(data["confidence"]))
        if data["favorites"]:
            qs = qs.filter(favorite__isnull=False)
        qs = qs.order_by(*ORDERINGS[data["sort"] or "encaje"])
        if data["open_now"]:
            now = timezone.localtime()
            return [c for c in qs if opening_for(c.opening_hours).is_open(now)]
        return qs


ORDERINGS = {
    "encaje": [F("enrichment__fit_score").desc(nulls_last=True), "-confidence_score", "name"],
    "confianza": ["-confidence_score", "name"],
    "nombre": ["name"],
}


def base_queryset() -> QuerySet[Company]:
    return Company.objects.filter(is_active=True).select_related(
        "category", "zone", "enrichment", "visit", "favorite"
    )
