from django import forms

from .models import Visit


class VisitForm(forms.ModelForm):
    class Meta:
        model = Visit
        fields = ["visited_on", "contact", "next_action", "next_action_on"]
        widgets = {
            "visited_on": forms.DateInput(attrs={"type": "date", "class": "field-input"}),
            "contact": forms.TextInput(
                attrs={"class": "field-input", "placeholder": "Nombre y cargo"}
            ),
            "next_action": forms.TextInput(
                attrs={"class": "field-input", "placeholder": "Llamar para preguntar por el CV…"}
            ),
            "next_action_on": forms.DateInput(attrs={"type": "date", "class": "field-input"}),
        }
