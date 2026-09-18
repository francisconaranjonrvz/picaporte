"""Proveedor Anthropic (Claude): PDF como documento y structured outputs nativos.

Opcional y de pago; se activa con LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY.
"""

import base64
import logging
from decimal import Decimal

import anthropic
import pydantic
from pydantic import BaseModel

from django.conf import settings

from .base import Completion, LLMError, LLMNotConfigured

logger = logging.getLogger(__name__)

# USD por millón de tokens (entrada, salida). Fuente: tarifas de la Anthropic API.
PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
}


class AnthropicProvider:
    name = "anthropic"
    default_model = "claude-haiku-4-5"

    def price_per_mtok(self, model: str) -> tuple[Decimal, Decimal]:
        return PRICES_PER_MTOK.get(model, (Decimal(0), Decimal(0)))

    def _client(self) -> anthropic.Anthropic:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMNotConfigured("Falta ANTHROPIC_API_KEY: la IA no está configurada.")
        # Sin reintentos: un reintento tras un timeout no cabría en los 60 s de la función.
        return anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT, max_retries=0
        )

    def complete[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        user_text: str,
        output_model: type[T],
        pdf_bytes: bytes | None,
        max_tokens: int,
    ) -> Completion[T]:
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
            response = self._client().messages.parse(
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
            # El SDK valida el JSON al parsear: una respuesta truncada o rechazada llega aquí.
            logger.warning("Respuesta no válida para %s: %s", output_model.__name__, exc)
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
        return Completion(output, response.usage.input_tokens, response.usage.output_tokens)
