"""Fixtures compartidas por toda la suite (pytest-django)."""

import pytest

from apps.core.testing import owner


@pytest.fixture
def user(db):
    """La cuenta principal de los tests ("laura"), dueña de perfil, favoritas y rutas."""
    return owner()


@pytest.fixture
def other_user(db):
    """Otra cuenta, para comprobar que nadie ve los datos de los demás."""
    return owner("marta")


@pytest.fixture
def auth_client(client, user):
    """Cliente de test ya autenticado."""
    client.force_login(user)
    return client
