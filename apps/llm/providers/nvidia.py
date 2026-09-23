"""Proveedor NVIDIA (build.nvidia.com): API OpenAI-compatible gratuita.

- Endpoint `https://integrate.api.nvidia.com/v1`, clave `nvapi-...` (NVIDIA_API_KEY).
- No acepta PDF: el texto se extrae con pypdf y va en el mensaje de usuario.
- JSON estructurado: esquema en el prompt + `response_format=json_object` (el
  endpoint alojado rechaza `nvext.guided_json`; si un modelo tampoco admite
  `response_format`, se reintenta sin él) + validación Pydantic con un intento
  de reparación si el JSON no valida.
- Modelo por defecto `google/gemma-4-31b-it`: en un benchmark con CV difícil
  (catalán, siglas, secciones desordenadas) acertó el 100 % de los campos en
  ~30 s; `openai/gpt-oss-20b` es la alternativa (76-88 %, 16-90 s). Los modelos
  "de razonamiento" se pasan pensando y agotan el tiempo.
- Modelo rápido (enriquecimiento de webs) `nvidia/nemotron-3.5-lightning-30b-a3b`
  con el razonamiento desactivado (`enable_thinking: false`): extracciones fieles
  en 5-90 s según la carga, cuando gemma y gpt-oss agotaban 120 s (sept. 2026).
  Con el razonamiento activo gasta todos los tokens pensando y no responde.
"""

import io
import json
import logging
import re
from decimal import Decimal

import openai
import pydantic
from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from django.conf import settings

from .base import Completion, LLMError, LLMNotConfigured

logger = logging.getLogger(__name__)

BASE_URL = "https://integrate.api.nvidia.com/v1"
# Modelos híbridos a los que hay que apagar el razonamiento para obtener solo el JSON.
NO_THINKING_MODELS = {"nvidia/nemotron-3.5-lightning-30b-a3b"}
MAX_DOCUMENT_CHARS = 40_000  # ~10k tokens: de sobra para un CV; evita facturas de contexto
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_pdf_text(data: bytes) -> str:
    """Texto plano del PDF (todas las páginas). Falla claro si es un escaneo sin texto."""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise LLMError("No se pudo leer el PDF (¿está dañado o protegido?).") from exc
    text = "\n\n".join(p.strip() for p in pages if p.strip()).strip()
    if not text:
        raise LLMError(
            "El PDF no contiene texto seleccionable (¿es un escaneo?). Exporta el CV como PDF de texto."
        )
    return text[:MAX_DOCUMENT_CHARS]


class NvidiaProvider:
    name = "nvidia"
    default_model = "google/gemma-4-31b-it"
    fast_model = "nvidia/nemotron-3.5-lightning-30b-a3b"

    def price_per_mtok(self, model: str) -> tuple[Decimal, Decimal]:
        return (Decimal(0), Decimal(0))  # nivel gratuito de build.nvidia.com

    def _client(self) -> openai.OpenAI:
        if not settings.NVIDIA_API_KEY:
            raise LLMNotConfigured("Falta NVIDIA_API_KEY: la IA no está configurada.")
        return openai.OpenAI(
            base_url=BASE_URL,
            api_key=settings.NVIDIA_API_KEY,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
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
        schema = output_model.model_json_schema()
        system_json = (
            f"{system}\n\nResponde únicamente con un objeto JSON con los datos pedidos (nunca el "
            f"esquema en sí), sin texto antes ni después, que cumpla exactamente este esquema "
            f"JSON:\n{json.dumps(schema, ensure_ascii=False)}"
        )
        user = user_text
        if pdf_bytes:
            user = f"{user_text}\n\n<documento>\n{extract_pdf_text(pdf_bytes)}\n</documento>"
        messages: list[dict] = [
            {"role": "system", "content": system_json},
            {"role": "user", "content": user},
        ]

        client = self._client()
        content, usage_in, usage_out = self._chat(client, model, messages, max_tokens)
        try:
            return Completion(_parse(output_model, content), usage_in, usage_out)
        except pydantic.ValidationError as exc:
            # Un intento de reparación: se le devuelve su salida y el error de validación.
            logger.info("JSON no válido de %s; se pide corrección: %s", model, exc)
            if _looks_like_schema(content):
                fix = "Has devuelto el esquema JSON, no los datos. Devuelve solo el objeto con los datos."
            else:
                fix = f"Tu respuesta no cumple el esquema. Devuelve solo el JSON corregido. Errores:\n{exc}"
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": fix},
            ]
            content2, in2, out2 = self._chat(client, model, messages, max_tokens)
            try:
                return Completion(_parse(output_model, content2), usage_in + in2, usage_out + out2)
            except pydantic.ValidationError as exc2:
                raise LLMError("La IA no devolvió un JSON válido tras dos intentos.") from exc2

    def _chat(self, client, model, messages, max_tokens) -> tuple[str, int, int]:
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "stream": False,
        }
        if model in NO_THINKING_MODELS:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        try:
            try:
                response = client.chat.completions.create(
                    **params, response_format={"type": "json_object"}
                )
            except openai.BadRequestError as exc:
                # Algún modelo alojado no admite response_format: el esquema del prompt basta.
                logger.info("response_format rechazado (%s); reintento sin él", exc.message)
                response = client.chat.completions.create(**params)
        except openai.AuthenticationError as exc:
            raise LLMError("La clave de la API de NVIDIA no es válida.") from exc
        except openai.RateLimitError as exc:
            raise LLMError("La API de NVIDIA está saturada; inténtalo en un minuto.") from exc
        except openai.APITimeoutError as exc:
            raise LLMError("La IA ha tardado demasiado; vuelve a intentarlo.") from exc
        except openai.APIStatusError as exc:
            logger.warning("NVIDIA API %s: %s", exc.status_code, exc.message)
            raise LLMError(f"Error de la API de NVIDIA ({exc.status_code}).") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("No se pudo conectar con la API de NVIDIA.") from exc

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("La respuesta de la IA se ha cortado; el documento es demasiado largo.")
        if choice.finish_reason == "content_filter":
            raise LLMError("La IA ha rechazado analizar este documento.")
        content = choice.message.content or ""
        usage = response.usage
        return (
            content,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )


def _looks_like_schema(content: str) -> bool:
    """Algunos modelos pequeños devuelven el esquema recibido en vez de rellenarlo."""
    return '"properties"' in content and '"type"' in content


def _parse[T: BaseModel](output_model: type[T], content: str) -> T:
    """Valida el JSON; tolera vallas ```json y texto alrededor del objeto."""
    text = FENCE_RE.sub("", content).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return output_model.model_validate_json(text)
