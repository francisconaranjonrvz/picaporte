from django.contrib.auth.forms import AuthenticationForm


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
