from decimal import Decimal
from types import SimpleNamespace

import anthropic
import pydantic
import pytest
from pydantic import BaseModel

from apps.llm import client as llm
from apps.llm.models import LLMCall


class Salida(BaseModel):
    nombre: str
    etiquetas: list[str]


class FakeMessages:
    """Sustituto de `client.messages` que devuelve una respuesta fija y cuenta llamadas."""

    def __init__(self, parsed=None, error=None, stop_reason="end_turn"):
        self.parsed = parsed
        self.error = error
        self.stop_reason = stop_reason
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            parsed_output=self.parsed,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
        )


@pytest.fixture
def fake(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.LLM_MODEL = "claude-haiku-4-5"
    fake = FakeMessages(parsed=Salida(nombre="Laura", etiquetas=["eventos"]))
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(messages=fake))
    return fake


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
def test_llama_a_la_api_y_registra_la_llamada(fake):
    result = _call()

    assert result.cached is False
    assert result.output.nombre == "Laura"
    call = LLMCall.objects.get()
    assert call.model == "claude-haiku-4-5"
    assert call.prompt_version == "cv_parse_v1"
    assert call.input_tokens == 1200
    assert call.output_tokens == 300
    assert call.cost_usd == Decimal("0.002700")  # 1200*1 + 300*5 por millón
    assert call.response == {"nombre": "Laura", "etiquetas": ["eventos"]}
    # El PDF viaja como documento base64 y el texto como bloque aparte.
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert content[1] == {"type": "text", "text": "hola"}


@pytest.mark.django_db
def test_segunda_llamada_identica_sale_de_la_cache(fake):
    _call()
    result = _call()

    assert result.cached is True
    assert result.output.nombre == "Laura"
    assert len(fake.calls) == 1
    assert LLMCall.objects.count() == 1


@pytest.mark.django_db
def test_cambiar_prompt_o_entrada_invalida_la_cache(fake):
    _call()
    _call(prompt_version="cv_parse_v2")
    _call(pdf_bytes=b"%PDF-1.4 otro")

    assert len(fake.calls) == 3
    assert LLMCall.objects.count() == 3


@pytest.mark.django_db
def test_sin_api_key_no_llama(settings):
    settings.ANTHROPIC_API_KEY = ""

    with pytest.raises(llm.LLMNotConfigured):
        _call()


@pytest.mark.django_db
def test_errores_de_la_api_se_traducen(fake):
    fake.error = anthropic.APIConnectionError(request=None)

    with pytest.raises(llm.LLMError, match="conectar"):
        _call()
    assert LLMCall.objects.count() == 0


@pytest.mark.django_db
def test_rechazo_o_salida_vacia_es_error(fake):
    fake.stop_reason = "refusal"
    with pytest.raises(llm.LLMError, match="rechazado"):
        _call()

    fake.stop_reason = "end_turn"
    fake.parsed = None
    with pytest.raises(llm.LLMError, match="válida"):
        _call()


def test_estimacion_de_coste_para_modelo_desconocido_es_cero():
    assert llm.estimate_cost("modelo-x", 1000, 1000) == 0
    assert llm.estimate_cost("claude-haiku-4-5", 1_000_000, 0) == Decimal("1.00")


def _status_error(cls, status):
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("boom", response=response, body=None)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("error", "fragmento"),
    [
        (lambda: _status_error(anthropic.AuthenticationError, 401), "clave"),
        (lambda: _status_error(anthropic.RateLimitError, 429), "saturada"),
        (lambda: _status_error(anthropic.InternalServerError, 500), "500"),
        (lambda: anthropic.APITimeoutError(request=None), "tardado"),
        (lambda: anthropic.APIConnectionError(request=None), "conectar"),
    ],
    ids=["auth", "rate-limit", "5xx", "timeout", "conexion"],
)
def test_cada_error_del_sdk_tiene_mensaje_amigable(fake, error, fragmento):
    fake.error = error()

    with pytest.raises(llm.LLMError, match=fragmento):
        _call()
    assert LLMCall.objects.count() == 0


@pytest.mark.django_db
def test_json_invalido_del_modelo_es_error_amigable(fake):
    # El SDK valida el JSON al parsear: una respuesta truncada llega como ValidationError.
    try:
        Salida.model_validate_json('{"nombre": "abc')
    except pydantic.ValidationError as exc:
        fake.error = exc

    with pytest.raises(llm.LLMError, match="truncada"):
        _call()


@pytest.mark.django_db
def test_max_tokens_es_error_amigable(fake):
    fake.stop_reason = "max_tokens"

    with pytest.raises(llm.LLMError, match="cortado"):
        _call()


@pytest.mark.django_db
def test_cambiar_el_esquema_invalida_la_cache(fake):
    class SalidaV2(BaseModel):
        nombre: str
        etiquetas: list[str]
        ciudad: str

    _call()
    fake.parsed = SalidaV2(nombre="Laura", etiquetas=[], ciudad="Barcelona")
    result = _call(output_model=SalidaV2)

    assert result.cached is False
    assert len(fake.calls) == 2


@pytest.mark.django_db
def test_fila_cacheada_incompatible_se_regenera(fake):
    call = _call().call
    LLMCall.objects.filter(pk=call.pk).update(response={"nombre": "Laura"})  # sin "etiquetas"

    result = _call()

    assert result.cached is False
    assert len(fake.calls) == 2
    assert LLMCall.objects.count() == 1


def test_client_real_se_construye_sin_reintentos(settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.LLM_TIMEOUT = 45

    client = llm._client()

    assert client.max_retries == 0
    assert client.timeout == 45
