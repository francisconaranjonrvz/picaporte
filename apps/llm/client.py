"""Capa común de llamadas al LLM: caché en BD y contabilidad de coste.

Toda llamada pasa por `call_structured`: construye un hash con proveedor,
modelo, versión del prompt, esquema de salida y entrada; si ya existe un
`LLMCall` con ese hash se devuelve sin llamar a la API. El proveedor real
(NVIDIA gratuito por defecto, Anthropic opcional) vive en `providers/`.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import pydantic
from pydantic import BaseModel

from django.conf import settings

from .models import LLMCall
from .providers import get_provider
from .providers.base import LLMError, LLMNotConfigured

logger = logging.getLogger(__name__)

__all__ = ["LLMError", "LLMNotConfigured", "LLMResult", "call_structured", "estimate_cost"]


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    output: T
    call: LLMCall
    cached: bool


def estimate_cost(
    price_per_mtok: tuple[Decimal, Decimal], input_tokens: int, output_tokens: int
) -> Decimal:
    price_in, price_out = price_per_mtok
    return (price_in * input_tokens + price_out * output_tokens) / Decimal(1_000_000)


def _input_hash(
    provider: str,
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
    for part in (provider, model, prompt_version, system, user_text, schema):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()


def call_structured[T: BaseModel](
    *,
    purpose: str,
    prompt_version: str,
    system: str,
    user_text: str,
    output_model: type[T],
    pdf_bytes: bytes | None = None,
    model: str | None = None,
    fast: bool = False,
    max_tokens: int = 4096,
) -> LLMResult[T]:
    """Llama al modelo (o devuelve la caché) y valida la salida con `output_model`.

    `fast=True` elige el modelo de volumen (`LLM_MODEL_FAST` o el rápido del proveedor).
    """
    provider = get_provider()
    if fast:
        model = model or settings.LLM_MODEL_FAST or provider.fast_model
    else:
        model = model or settings.LLM_MODEL or provider.default_model
    payload = pdf_bytes or b""
    input_hash = _input_hash(
        provider.name, model, prompt_version, system, user_text, output_model, payload
    )

    cached = LLMCall.objects.filter(input_hash=input_hash).first()
    if cached is not None:
        try:
            return LLMResult(output_model.model_validate(cached.response), cached, True)
        except pydantic.ValidationError:
            # Fila incompatible (no debería ocurrir: el esquema va en el hash). Se regenera.
            logger.warning("LLMCall %s no valida contra %s; se descarta", cached.pk, output_model)
            cached.delete()

    completion = provider.complete(
        model=model,
        system=system,
        user_text=user_text,
        output_model=output_model,
        pdf_bytes=pdf_bytes,
        max_tokens=max_tokens,
    )
    call = LLMCall.objects.create(
        purpose=purpose,
        model=model,
        prompt_version=prompt_version,
        input_hash=input_hash,
        response=completion.output.model_dump(mode="json"),
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        cost_usd=estimate_cost(
            provider.price_per_mtok(model), completion.input_tokens, completion.output_tokens
        ),
    )
    return LLMResult(completion.output, call, False)
