from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "Picaporte"

    def ready(self) -> None:
        # Registra los system checks propios (p. ej. DEBUG activo fuera de local).
        from . import checks  # noqa: F401
