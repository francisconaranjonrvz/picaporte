from types import SimpleNamespace

import anthropic
import pydantic
import pytest
from pydantic import BaseModel

from apps.llm.providers.anthropic import AnthropicProvider
from apps.llm.providers.base import LLMError, LLMNotConfigured


class Salida(BaseModel):
    nombre: str


class FakeMessages:
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
def provider(monkeypatch, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    fake = FakeMessages(parsed=Salida(nombre="Laura"))
    monkeypatch.setattr(AnthropicProvider, "_client", lambda self: SimpleNamespace(messages=fake))
    p = AnthropicProvider()
    p.fake = fake
    return p


def _complete(provider, **overrides):
    params = {
        "model": "claude-haiku-4-5",
        "system": "extrae",
        "user_text": "hola",
        "output_model": Salida,
        "pdf_bytes": b"%PDF-1.4 test",
        "max_tokens": 1024,
    }
    params.update(overrides)
    return provider.complete(**params)


def test_envia_el_pdf_como_documento_y_devuelve_uso(provider):
    completion = _complete(provider)

    assert completion.output.nombre == "Laura"
    assert (completion.input_tokens, completion.output_tokens) == (1200, 300)
    content = provider.fake.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert content[1] == {"type": "text", "text": "hola"}
    assert provider.fake.calls[0]["output_format"] is Salida


def _status_error(cls, status):
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


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
def test_cada_error_del_sdk_tiene_mensaje_amigable(provider, error, fragmento):
    provider.fake.error = error()

    with pytest.raises(LLMError, match=fragmento):
        _complete(provider)


def test_json_invalido_es_error_amigable(provider):
    try:
        Salida.model_validate_json('{"nombre": "abc')
    except pydantic.ValidationError as exc:
        provider.fake.error = exc

    with pytest.raises(LLMError, match="truncada"):
        _complete(provider)


@pytest.mark.parametrize(
    ("stop_reason", "fragmento"), [("refusal", "rechazado"), ("max_tokens", "cortado")]
)
def test_stop_reason_problematico(provider, stop_reason, fragmento):
    provider.fake.stop_reason = stop_reason

    with pytest.raises(LLMError, match=fragmento):
        _complete(provider)


def test_salida_vacia(provider):
    provider.fake.parsed = None

    with pytest.raises(LLMError, match="válida"):
        _complete(provider)


def test_sin_api_key(settings):
    settings.ANTHROPIC_API_KEY = ""

    with pytest.raises(LLMNotConfigured):
        AnthropicProvider()._client()


def test_client_real_sin_reintentos(settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.LLM_TIMEOUT = 45

    client = AnthropicProvider()._client()

    assert client.max_retries == 0
    assert client.timeout == 45
    assert AnthropicProvider().price_per_mtok("claude-haiku-4-5")[0] == 1
