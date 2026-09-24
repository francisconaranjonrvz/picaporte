"""Filtros de la lista de Explorar y del mapa (mismos parámetros GET en ambos).

Encaje, estado, favoritas y ofertas son del usuario que mira (`personal.for_user`).
"""

from django import forms
from django.db.models import Exists, F, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.catalog.models import Category, Zone
from apps.offers.models import active_offers
from apps.tracking.models import Visit

from .models import Company
from .opening import opening_for
from .personal import for_user

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
    q = forms.CharField(required=False, label="Buscar")
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
    offers = forms.BooleanField(required=False, label="Con ofertas")
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES, label="Orden")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["category"].queryset = Category.objects.filter(is_active=True)
        self.fields["zone"].queryset = Zone.objects.filter(is_active=True)
        self.fields["status"].choices = [
            ("", "Cualquier estado"),
            ("sin_estado", "Sin empezar"),
            *Visit.Status.choices,
        ]

    def clean_q(self) -> str:
        return self.cleaned_data["q"][:80]  # una búsqueda larguísima se recorta, no invalida

    def values(self) -> dict:
        """Valores limpios; un campo inválido (o un formulario sin datos) cuenta como vacío.

        Así un parámetro que ya no vale (p. ej. una categoría desactivada) no anula los demás.
        """
        self.is_valid()
        cleaned = getattr(self, "cleaned_data", {})
        return {name: cleaned.get(name) for name in self.fields}

    @property
    def active_count(self) -> int:
        return sum(1 for k, v in self.values().items() if v and k not in {"sort", "q"})

    def apply(
        self, qs: QuerySet[Company] | None = None, after: Company | None = None
    ) -> list[Company] | QuerySet[Company]:
        """Aplica los filtros. "Abierto ahora" se evalúa en Python (horarios OSM): devuelve lista.

        `after`: solo las empresas que van detrás de esa en el orden elegido ("Cargar más").
        """
        qs = qs if qs is not None else base_queryset(self.user)
        data = self.values()
        if data["q"]:
            qs = qs.filter(
                Q(name__icontains=data["q"]) | Q(enrichment__services__icontains=data["q"])
            )
        if data["score"]:
            qs = qs.filter(fit__gte=int(data["score"]))
        if data["category"]:
            qs = qs.filter(category=data["category"])
        if data["zone"]:
            qs = qs.filter(zone=data["zone"])
        if data["status"] == "sin_estado":
            qs = qs.filter(Q(visit_status__isnull=True) | Q(visit_status=Visit.Status.PENDING))
        elif data["status"]:
            qs = qs.filter(visit_status=data["status"])
        if data["catalan"] == "requerido":
            qs = qs.filter(enrichment__requires_catalan="si")
        elif data["catalan"] == "no_requerido":
            qs = qs.exclude(enrichment__requires_catalan="si")
        if data["confidence"]:
            qs = qs.filter(confidence_score__gte=int(data["confidence"]))
        if data["favorites"]:
            qs = qs.filter(is_favorite=True)
        if data["offers"]:
            qs = qs.filter(has_offers=True)
        sort = data["sort"] or "encaje"
        if after is not None:
            qs = qs.filter(_after(sort, after))
        qs = qs.order_by(*ORDERINGS[sort])
        if data["open_now"]:
            now = timezone.localtime()
            return [c for c in qs if opening_for(c.opening_hours).is_open(now)]
        return qs


# El pk final desempata: el orden es total y "Cargar más" puede seguir desde una empresa.
ORDERINGS = {
    "encaje": [
        F("fit").desc(nulls_last=True),
        "-confidence_score",
        "name",
        "pk",
    ],
    "confianza": ["-confidence_score", "name", "pk"],
    "nombre": ["name", "pk"],
}


def _after(sort: str, anchor: Company) -> Q:
    """Empresas que van detrás de `anchor` en el orden `sort` (paginación por cursor, sin OFFSET).

    El cursor no depende de que `anchor` siga en la lista: si se quita de favoritas o cierra
    mientras tanto, la siguiente tanda no se salta ni repite tarjetas.
    """
    by_name = Q(name__gt=anchor.name) | Q(name=anchor.name, pk__gt=anchor.pk)
    if sort == "nombre":
        return by_name
    confidence = anchor.confidence_score
    by_confidence = Q(confidence_score__lt=confidence) | (Q(confidence_score=confidence) & by_name)
    if sort == "confianza":
        return by_confidence
    score = getattr(anchor, "fit", None)  # `anchor` viene anotado por `for_user`
    if score is None:  # los sin puntuar van al final
        return Q(fit__isnull=True) & by_confidence
    return Q(fit__lt=score) | Q(fit__isnull=True) | (Q(fit=score) & by_confidence)


def base_queryset(user) -> QuerySet[Company]:
    qs = Company.objects.filter(is_active=True).select_related("category", "zone", "enrichment")
    return for_user(qs, user).annotate(
        has_offers=Exists(active_offers(user).filter(company=OuterRef("pk")))
    )
