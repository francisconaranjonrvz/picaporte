from decimal import Decimal

import pytest
from pydantic import BaseModel

from apps.llm import client as llm
from apps.llm.models import LLMCall
from apps.llm.providers.base import Completion


class Salida(BaseModel):
    nombre: str
    etiquetas: list[str]


class FakeProvider:
    """Proveedor de pruebas: devuelve una salida fija y cuenta llamadas."""

    name = "fake"
    default_model = "fake-model"

    def __init__(self, output, price=(Decimal("1.00"), Decimal("5.00"))):
        self.output = output
        self.price = price
        self.calls = []

    def price_per_mtok(self, model):
        return self.price

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return Completion(self.output, 1200, 300)


@pytest.fixture
def fake(monkeypatch, settings):
    settings.LLM_MODEL = ""
    provider = FakeProvider(Salida(nombre="Laura", etiquetas=["eventos"]))
    monkeypatch.setattr(llm, "get_provider", lambda: provider)
    return provider


def _call(**overrides):
    params = {
        "purpose": "cv_parse",
        "prompt_version": "cv_parse_v1",
        "system": "extrae",
        "user_text": "hola",
        "output_model": Salida,
        "pdf_bytes": b"%PDF-1.4 test",
    }
    params.update(overrides)
    return llm.call_structured(**params)


@pytest.mark.django_db
def test_llama_al_proveedor_y_registra_la_llamada(fake):
    result = _call()

    assert result.cached is False
    assert result.output.nombre == "Laura"
    call = LLMCall.objects.get()
    assert call.model == "fake-model"  # modelo por defecto del proveedor
    assert call.prompt_version == "cv_parse_v1"
    assert call.input_tokens == 1200
    assert call.output_tokens == 300
    assert call.cost_usd == Decimal("0.002700")  # 1200*1 + 300*5 por millón
    assert call.response == {"nombre": "Laura", "etiquetas": ["eventos"]}
    assert fake.calls[0]["pdf_bytes"] == b"%PDF-1.4 test"
    assert fake.calls[0]["system"] == "extrae"


@pytest.mark.django_db
def test_llm_model_de_settings_tiene_prioridad(fake, settings):
    settings.LLM_MODEL = "otro/modelo"

    assert _call().call.model == "otro/modelo"


@pytest.mark.django_db
def test_proveedor_gratuito_registra_coste_cero(fake):
    fake.price = (Decimal(0), Decimal(0))

    assert _call().call.cost_usd == 0


@pytest.mark.django_db
def test_segunda_llamada_identica_sale_de_la_cache(fake):
    _call()
    result = _call()

    assert result.cached is True
    assert result.output.nombre == "Laura"
    assert len(fake.calls) == 1
    assert LLMCall.objects.count() == 1


@pytest.mark.django_db
def test_cambiar_prompt_entrada_o_esquema_invalida_la_cache(fake):
    class SalidaV2(BaseModel):
        nombre: str
        etiquetas: list[str]
        ciudad: str

    _call()
    _call(prompt_version="cv_parse_v2")
    _call(pdf_bytes=b"%PDF-1.4 otro")
    fake.output = SalidaV2(nombre="Laura", etiquetas=[], ciudad="Barcelona")
    _call(output_model=SalidaV2)

    assert len(fake.calls) == 4
    assert LLMCall.objects.count() == 4


@pytest.mark.django_db
def test_fila_cacheada_incompatible_se_regenera(fake):
    call = _call().call
    LLMCall.objects.filter(pk=call.pk).update(response={"nombre": "Laura"})  # sin "etiquetas"

    result = _call()

    assert result.cached is False
    assert len(fake.calls) == 2
    assert LLMCall.objects.count() == 1


@pytest.mark.django_db
def test_los_errores_del_proveedor_no_dejan_rastro(fake):
    def falla(**kwargs):
        raise llm.LLMError("boom")

    fake.complete = falla

    with pytest.raises(llm.LLMError, match="boom"):
        _call()
    assert LLMCall.objects.count() == 0


def test_estimacion_de_coste():
    assert llm.estimate_cost((Decimal(0), Decimal(0)), 1000, 1000) == 0
    assert llm.estimate_cost((Decimal("1.00"), Decimal("5.00")), 1_000_000, 0) == Decimal("1.00")


def test_proveedor_desconocido_falla_claro(settings):
    from apps.llm.providers import get_provider

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        get_provider("gpt")
    assert get_provider("nvidia").name == "nvidia"
    assert get_provider("anthropic").name == "anthropic"
