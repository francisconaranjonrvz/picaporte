"""Formularios del perfil, pensados para editar en el móvil.

Las listas JSON del modelo se editan como texto (una entrada por línea o
separadas por comas) y se convierten de ida y vuelta aquí; las preferencias
son grupos de chips (checkboxes).
"""

import re

from django import forms

from apps.catalog.models import Category, Zone

from .models import CompanySize, Profile, WorkLanguage

SEP = " · "
SUMMARY_SEP = " — "
SEP_RE = re.compile(r"\s*·\s*")


class ListField(forms.CharField):
    """Lista de cadenas separadas por comas o saltos de línea <-> list[str]."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.Textarea(attrs={"rows": 2}))
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return value

    def to_python(self, value):
        if isinstance(value, list):
            return value
        text = super().to_python(value) or ""
        items = [part.strip() for chunk in text.splitlines() for part in chunk.split(",")]
        return [item for item in items if item]


class RecordsField(forms.CharField):
    """Una entrada por línea con campos separados por ' · ' <-> list[dict].

    `keys` fija el orden de los campos; el último puede ir tras ' — '
    (resumen) para que la línea siga siendo legible.
    """

    def __init__(self, keys: tuple[str, ...], summary_key: str | None = None, **kwargs):
        self.keys = keys
        self.summary_key = summary_key
        kwargs.setdefault("required", False)
        kwargs.setdefault("widget", forms.Textarea(attrs={"rows": 3}))
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if not isinstance(value, list):
            return value
        lines = []
        for record in value:
            parts = [str(record.get(k, "")).strip() for k in self.keys]
            while parts and not parts[-1]:  # sin separadores colgando al final de la línea
                parts.pop()
            main = SEP.join(parts)
            summary = str(record.get(self.summary_key, "")).strip() if self.summary_key else ""
            lines.append(f"{main}{SUMMARY_SEP}{summary}" if summary else main)
        return "\n".join(lines)

    def to_python(self, value):
        if isinstance(value, list):
            return value
        text = super().to_python(value) or ""
        records = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            main, _, summary = line.partition(SUMMARY_SEP)
            parts = [p.strip() for p in SEP_RE.split(main)]
            record = {key: (parts[i] if i < len(parts) else "") for i, key in enumerate(self.keys)}
            if self.summary_key:
                record[self.summary_key] = summary.strip()
            records.append(record)
        return records


class ChipSelect(forms.CheckboxSelectMultiple):
    template_name = "profiles/widgets/chip_select.html"
    option_template_name = "profiles/widgets/chip_option.html"


class ProfileForm(forms.ModelForm):
    skills = ListField(label="Habilidades", help_text="Separadas por comas.")
    languages = RecordsField(
        keys=("language", "level"),
        label="Idiomas",
        help_text="Uno por línea: Idioma · nivel",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    education = RecordsField(
        keys=("title", "organization", "period"),
        label="Formación",
        help_text="Una por línea: Título · Centro · Periodo",
    )
    experience = RecordsField(
        keys=("title", "organization", "period"),
        summary_key="summary",
        label="Experiencia",
        help_text="Una por línea: Puesto · Empresa · Periodo — resumen",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    categories = forms.ModelMultipleChoiceField(
        queryset=Category.objects.filter(is_active=True),
        required=False,
        widget=ChipSelect,
        label="Sectores",
        help_text=(
            "Agencias, estudios y productoras se buscan siempre. Medios, editoriales, "
            "fotografía, música y cultura solo si los marcas. Todos cuentan para tu ranking."
        ),
    )
    zones = forms.ModelMultipleChoiceField(
        queryset=Zone.objects.filter(is_active=True),
        required=False,
        widget=ChipSelect,
        label="Zonas",
    )
    company_sizes = forms.MultipleChoiceField(
        choices=CompanySize.choices, required=False, widget=ChipSelect, label="Tamaño de empresa"
    )
    work_languages = forms.MultipleChoiceField(
        choices=WorkLanguage.choices, required=False, widget=ChipSelect, label="Idiomas de trabajo"
    )

    class Meta:
        model = Profile
        fields = [
            "full_name",
            "headline",
            "summary",
            "education",
            "experience",
            "skills",
            "languages",
            "categories",
            "zones",
            "company_sizes",
            "work_languages",
            "interests",
        ]
        labels = {
            "full_name": "Nombre",
            "headline": "Titular",
            "summary": "Resumen",
            "interests": "Intereses",
        }
        help_texts = {
            "headline": "Una línea que te presente (aparece en el gancho para cada empresa).",
            "interests": "Texto libre: qué tipo de proyectos, marcas o ambientes te atraen.",
        }
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "headline": forms.TextInput(attrs={"maxlength": 120}),
            "summary": forms.Textarea(attrs={"rows": 3}),
            "interests": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, ChipSelect):
                field.widget.attrs.setdefault("class", "field-input")
                field.widget.attrs.setdefault("id", f"id_{name}")


class CVUploadForm(forms.Form):
    cv = forms.FileField(
        label="CV en PDF", widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"})
    )
