"""Ayudas para los tests (no se usa en la app)."""

from django.contrib.auth import get_user_model


def owner(username: str = "laura"):
    """La cuenta de pruebas; se crea la primera vez que se pide (fixtures o helpers)."""
    User = get_user_model()
    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secreta-123",  # noqa: S106
        )
    return user
