from django import forms
from django.utils import timezone

from apps.catalog.models import Zone

from .models import Route
from .planner import DEFAULT_STOPS, MAX_STOPS, MIN_STOPS


class RouteForm(forms.Form):
    date = forms.DateField(
        label="Día",
        widget=forms.DateInput(attrs={"type": "date", "class": "field-input"}, format="%Y-%m-%d"),
    )
    slot = forms.ChoiceField(
        label="Franja",
        choices=Route.Slot.choices,
        widget=forms.Select(attrs={"class": "field-input px-3"}),
    )
    zone = forms.ModelChoiceField(
        label="Zona",
        queryset=Zone.objects.none(),
        required=False,
        empty_label="Toda Barcelona",
        widget=forms.Select(attrs={"class": "field-input px-3"}),
    )
    size = forms.IntegerField(
        label="Paradas",
        min_value=MIN_STOPS,
        max_value=MAX_STOPS,
        initial=DEFAULT_STOPS,
        widget=forms.NumberInput(attrs={"class": "field-input", "inputmode": "numeric"}),
    )
    # Rellenados por el navegador si se permite la ubicación (salida desde donde estás).
    start_lat = forms.FloatField(
        required=False, min_value=41.2, max_value=41.6, widget=forms.HiddenInput
    )
    start_lng = forms.FloatField(
        required=False, min_value=1.9, max_value=2.4, widget=forms.HiddenInput
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("initial", {"date": timezone.localdate(), "size": DEFAULT_STOPS})
        super().__init__(*args, **kwargs)
        self.fields["zone"].queryset = Zone.objects.filter(is_active=True)

    @property
    def start(self) -> tuple[float, float] | None:
        lat, lng = self.cleaned_data.get("start_lat"), self.cleaned_data.get("start_lng")
        return (lat, lng) if lat is not None and lng is not None else None
