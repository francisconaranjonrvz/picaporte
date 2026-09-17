"""Fixtures compartidas por toda la suite (pytest-django)."""

import pytest


@pytest.fixture
def user(db, django_user_model):
    """El usuario único de la app, creado en la BD de test."""
    return django_user_model.objects.create_user(
        username="laura", email="laura@example.com", password="secreta-123"
    )


@pytest.fixture
def auth_client(client, user):
    """Cliente de test ya autenticado."""
    client.force_login(user)
    return client
