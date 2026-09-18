"""Cliente de la Anthropic API con caché en BD y contabilidad de coste.

Toda llamada pasa por `call_structured`: construye un hash con modelo,
versión del prompt, esquema de salida y entrada; si ya existe un `LLMCall`
con ese hash se devuelve sin llamar a la API. La salida siempre se valida
con Pydantic (structured outputs), así que lo que llega a la BD ya tiene la
forma esperada.
"""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import anthropic
import pydantic
from pydantic import BaseModel

from django.conf import settings

from .models import LLMCall

logger = logging.getLogger(__name__)

# USD por millón de tokens (entrada, salida). Fuente: tarifas de la Anthropic API.
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
}


class LLMError(Exception):
    """Error amigable para la UI (configuración, red o respuesta inválida)."""


class LLMNotConfigured(LLMError):
    pass


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    call: LLMCall
    cached: bool


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price_in, price_out = PRICES_PER_MTOK.get(model, (Decimal(0), Decimal(0)))
    return (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)


def _input_hash(
    model: str,
    prompt_version: str,
    system: str,
    user_text: str,
    output_model: type[BaseModel],
    payload: bytes,
) -> str:
    """El esquema forma parte de la clave: cambiarlo invalida la caché igual que el prompt."""
    schema = json.dumps(output_model.model_json_schema(), sort_keys=True)
    digest = hashlib.sha256()
    for part in (model, prompt_version, system, user_text, schema):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()


def _client() -> anthropic.Anthropic:
    if not settings.ANTHROPIC_API_KEY:
        raise LLMNotConfigured(
            "Falta ANTHROPIC_API_KEY: la IA no está configurada en este entorno."
        )
    # Sin reintentos: un reintento tras un timeout de 45 s no cabría en los 60 s de la función.
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.LLM_TIMEOUT,
        max_retries=0,
    )


def call_structured[T: BaseModel](
    *,
    purpose: str,
    prompt_version: str,
    system: str,
    user_text: str,
    output_model: type[T],
    pdf_bytes: bytes | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> LLMResult[T]:
    """Llama al modelo (o devuelve la caché) y valida la salida con `output_model`."""
    model = model or settings.LLM_MODEL
    payload = pdf_bytes or b""
    input_hash = _input_hash(model, prompt_version, system, user_text, output_model, payload)

    cached = LLMCall.objects.filter(input_hash=input_hash).first()
    if cached is not None:
        try:
            return LLMResult(output_model.model_validate(cached.response), cached, True)
        except pydantic.ValidationError:
            # Fila incompatible (no debería ocurrir: el esquema va en el hash). Se regenera.
            logger.warning("LLMCall %s no valida contra %s; se descarta", cached.pk, output_model)
            cached.delete()

    content: list[dict] = []
    if pdf_bytes:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": user_text})

    try:
        response = _client().messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError("La clave de la Anthropic API no es válida.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("La API está saturada; inténtalo en un minuto.") from exc
    except anthropic.APITimeoutError as exc:
        raise LLMError("La IA ha tardado demasiado; vuelve a intentarlo.") from exc
    except anthropic.APIStatusError as exc:
        logger.warning("Anthropic API %s: %s", exc.status_code, exc.message)
        raise LLMError(f"Error de la Anthropic API ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("No se pudo conectar con la Anthropic API.") from exc
    except pydantic.ValidationError as exc:
        # El SDK valida el JSON al parsear: una respuesta truncada (max_tokens) o rechazada
        # con texto no JSON llega aquí, no como error de la API.
        logger.warning("Respuesta de la IA no válida para %s: %s", output_model.__name__, exc)
        raise LLMError(
            "La IA no devolvió una respuesta válida (¿respuesta truncada o rechazada?)."
        ) from exc

    if response.stop_reason == "refusal":
        raise LLMError("La IA ha rechazado analizar este documento.")
    if response.stop_reason == "max_tokens":
        raise LLMError("La respuesta de la IA se ha cortado; el documento es demasiado largo.")
    output = response.parsed_output
    if output is None:
        raise LLMError("La IA no devolvió una respuesta válida.")

    usage = response.usage
    call = LLMCall.objects.create(
        purpose=purpose,
        model=model,
        prompt_version=prompt_version,
        input_hash=input_hash,
        response=output.model_dump(mode="json"),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=estimate_cost(model, usage.input_tokens, usage.output_tokens),
    )
    return LLMResult(output, call, False)
