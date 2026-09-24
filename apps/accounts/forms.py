from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Formulario de acceso con las clases del design system y autocompletado móvil."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["username"].widget.attrs.update(
            {
                "class": "field-input",
                "autocomplete": "username",
                "autocapitalize": "none",
                "autofocus": True,
            }
        )
        self.fields["password"].label = "Contraseña"
        self.fields["password"].widget.attrs.update(
            {"class": "field-input", "autocomplete": "current-password"}
        )


class RegisterForm(UserCreationForm):
    """Alta abierta: usuario y contraseña (validada con AUTH_PASSWORD_VALIDATORS).

    `website` es una trampa para bots: está oculto y una persona lo deja vacío.
    """

    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["username"].help_text = "Letras, números y @ . + - _ (máx. 150)."
        self.fields["username"].widget.attrs.update(
            {"class": "field-input", "autocomplete": "username", "autocapitalize": "none"}
        )
        for name, label in (("password1", "Contraseña"), ("password2", "Repite la contraseña")):
            self.fields[name].label = label
            self.fields[name].widget.attrs.update(
                {"class": "field-input", "autocomplete": "new-password"}
            )
        self.fields[
            "password1"
        ].help_text = "Al menos 8 caracteres, no solo números ni demasiado común."
        self.fields["password2"].help_text = ""

    def clean(self):
        # Error general (no del campo): el campo trampa está oculto y su error no se vería.
        if self.data.get("website"):
            raise forms.ValidationError("Formulario no válido.")
        return super().clean()
