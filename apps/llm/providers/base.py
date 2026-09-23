from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel


class LLMError(Exception):
    """Error amigable para la UI (configuración, red o respuesta inválida)."""


class LLMNotConfigured(LLMError):
    pass


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    output: T
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str
    default_model: str
    fast_model: str  # para tareas de volumen (enriquecimiento)

    def price_per_mtok(self, model: str) -> tuple[Decimal, Decimal]:
        """USD por millón de tokens (entrada, salida); (0, 0) si es gratuito."""
        ...

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
        """Una llamada al modelo con salida validada; lanza LLMError si algo falla."""
        ...
