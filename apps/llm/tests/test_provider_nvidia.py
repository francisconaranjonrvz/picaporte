import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from apps.llm.providers.base import LLMError, LLMNotConfigured
from apps.llm.providers.nvidia import BASE_URL, NvidiaProvider, extract_pdf_text

from .pdf_factory import make_blank_pdf, make_text_pdf


class Salida(BaseModel):
    nombre: str
    etiquetas: list[str]


def _response(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
        ],
        usage=SimpleNamespace(prompt_tokens=800, completion_tokens=120),
    )


class FakeCompletions:
    """Sustituto de `client.chat.completions`: cola de respuestas/errores y registro de llamadas."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def provider(monkeypatch, settings):
    settings.NVIDIA_API_KEY = "nvapi-test"
    fake = FakeCompletions(_response('{"nombre": "Laura", "etiquetas": ["eventos"]}'))
    monkeypatch.setattr(
        NvidiaProvider,
        "_client",
        lambda self: SimpleNamespace(chat=SimpleNamespace(completions=fake)),
    )
    p = NvidiaProvider()
    p.fake = fake
    return p


def _complete(provider, **overrides):
    params = {
        "model": "meta/llama-3.3-70b-instruct",
        "system": "extrae",
        "user_text": "Extrae los datos.",
        "output_model": Salida,
        "pdf_bytes": make_text_pdf("Laura Vidal", "Publicidad y RRPP"),
        "max_tokens": 1024,
    }
    params.update(overrides)
    return provider.complete(**params)


def test_extrae_texto_del_pdf():
    text = extract_pdf_text(make_text_pdf("Laura Vidal", "Publicidad y RRPP"))

    assert "Laura Vidal" in text
    assert "Publicidad y RRPP" in text


def test_pdf_sin_texto_o_danado_falla_claro():
    with pytest.raises(LLMError, match="escaneo"):
        extract_pdf_text(make_blank_pdf())
    with pytest.raises(LLMError, match="leer el PDF"):
        extract_pdf_text(b"%PDF-1.4 roto")


def test_envia_texto_del_pdf_esquema_y_guided_json(provider):
    completion = _complete(provider)

    assert completion.output == Salida(nombre="Laura", etiquetas=["eventos"])
    assert (completion.input_tokens, completion.output_tokens) == (800, 120)
    call = provider.fake.calls[0]
    assert call["model"] == "meta/llama-3.3-70b-instruct"
    assert call["response_format"] == {"type": "json_object"}
    assert "extra_body" not in call
    system, user = call["messages"]
    assert system["role"] == "system"
    assert "extrae" in system["content"]
    assert '"etiquetas"' in system["content"]  # esquema JSON incrustado en el prompt
    assert user["content"].startswith("Extrae los datos.")
    assert "<documento>" in user["content"]
    assert "Laura Vidal" in user["content"]


def test_sin_pdf_envia_solo_el_texto(provider):
    _complete(provider, pdf_bytes=None)

    assert "<documento>" not in provider.fake.calls[0]["messages"][1]["content"]


def test_tolera_vallas_markdown_y_texto_alrededor(provider):
    provider.fake.outcomes = [
        _response('Claro:\n```json\n{"nombre": "Laura", "etiquetas": []}\n```')
    ]

    assert _complete(provider).output.nombre == "Laura"


def test_repara_json_invalido_una_vez(provider):
    provider.fake.outcomes = [
        _response('{"nombre": "Laura"}'),  # falta "etiquetas"
        _response('{"nombre": "Laura", "etiquetas": ["eventos"]}'),
    ]

    completion = _complete(provider)

    assert completion.output.etiquetas == ["eventos"]
    assert completion.input_tokens == 1600  # suma de los dos intentos
    repair = provider.fake.calls[1]["messages"]
    assert repair[2]["role"] == "assistant"
    assert "no cumple el esquema" in repair[3]["content"]


def test_si_devuelve_el_esquema_se_le_dice_claramente(provider):
    schema_echo = '{"properties": {"nombre": {"type": "string"}}, "type": "object"}'
    provider.fake.outcomes = [
        _response(schema_echo),
        _response('{"nombre": "Laura", "etiquetas": []}'),
    ]

    assert _complete(provider).output.nombre == "Laura"
    repair = provider.fake.calls[1]["messages"][3]["content"]
    assert "esquema JSON, no los datos" in repair
    assert "nunca el esquema" in provider.fake.calls[0]["messages"][0]["content"]


def test_dos_json_invalidos_es_error(provider):
    provider.fake.outcomes = [_response("nada"), _response("{}")]

    with pytest.raises(LLMError, match="dos intentos"):
        _complete(provider)


def _status_error(cls, status, message="boom"):
    request = httpx.Request("POST", f"{BASE_URL}/chat/completions")
    response = httpx.Response(status, request=request, json={"error": message})
    return cls(message, response=response, body=None)


def test_si_response_format_no_se_admite_reintenta_sin_el(provider):
    provider.fake.outcomes = [
        _status_error(openai.BadRequestError, 400, "response_format not supported"),
        _response('{"nombre": "Laura", "etiquetas": []}'),
    ]

    assert _complete(provider).output.nombre == "Laura"
    assert "response_format" not in provider.fake.calls[1]


@pytest.mark.parametrize(
    ("error", "fragmento"),
    [
        (lambda: _status_error(openai.AuthenticationError, 401), "clave"),
        (lambda: _status_error(openai.RateLimitError, 429), "saturada"),
        (lambda: _status_error(openai.InternalServerError, 500), "500"),
        (lambda: openai.APITimeoutError(request=httpx.Request("POST", BASE_URL)), "tardado"),
        (
            lambda: openai.APIConnectionError(request=httpx.Request("POST", BASE_URL)),
            "conectar",
        ),
    ],
    ids=["auth", "rate-limit", "5xx", "timeout", "conexion"],
)
def test_cada_error_del_sdk_tiene_mensaje_amigable(provider, error, fragmento):
    provider.fake.outcomes = [error()]

    with pytest.raises(LLMError, match=fragmento):
        _complete(provider)


@pytest.mark.parametrize(
    ("finish_reason", "fragmento"), [("length", "cortado"), ("content_filter", "rechazado")]
)
def test_finish_reason_problematico(provider, finish_reason, fragmento):
    provider.fake.outcomes = [_response("{}", finish_reason=finish_reason)]

    with pytest.raises(LLMError, match=fragmento):
        _complete(provider)


def test_sin_api_key(settings):
    settings.NVIDIA_API_KEY = ""

    with pytest.raises(LLMNotConfigured, match="NVIDIA_API_KEY"):
        NvidiaProvider()._client()


def test_client_real_apunta_a_nvidia_sin_reintentos(settings):
    settings.NVIDIA_API_KEY = "nvapi-test"
    settings.LLM_TIMEOUT = 45

    client = NvidiaProvider()._client()

    assert str(client.base_url).rstrip("/") == BASE_URL
    assert client.max_retries == 0
    assert client.timeout == 45
    assert NvidiaProvider().price_per_mtok("google/gemma-4-31b-it") == (0, 0)
    assert NvidiaProvider().default_model == "google/gemma-4-31b-it"
    assert NvidiaProvider().fast_model == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_modelo_rapido_va_sin_razonamiento(provider):
    _complete(provider, model="nvidia/nemotron-3.5-lightning-30b-a3b", pdf_bytes=None)

    call = provider.fake.calls[0]
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert call["response_format"] == {"type": "json_object"}


def test_el_esquema_del_prompt_es_json_valido(provider):
    _complete(provider)

    system = provider.fake.calls[0]["messages"][0]["content"]
    schema_text = system[system.index("{") :]
    assert json.loads(schema_text)["properties"]["nombre"]["type"] == "string"
