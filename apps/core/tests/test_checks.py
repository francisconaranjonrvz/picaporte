from django.core import checks

from apps.core.checks import debug_outside_local


def test_avisa_si_debug_fuera_de_local(settings):
    settings.DEBUG = True
    settings.APP_ENV = "production"

    [warning] = debug_outside_local(None)

    assert warning.id == "picaporte.W001"
    assert warning.level == checks.WARNING


def test_no_avisa_en_local_ni_sin_debug(settings):
    settings.DEBUG = True
    settings.APP_ENV = "local"
    assert debug_outside_local(None) == []

    settings.DEBUG = False
    settings.APP_ENV = "production"
    assert debug_outside_local(None) == []
