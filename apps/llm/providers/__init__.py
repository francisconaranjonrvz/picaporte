"""Proveedores de LLM intercambiables (settings.LLM_PROVIDER).

Cada proveedor implementa `complete()` y devuelve la salida ya validada con
Pydantic más el uso de tokens. La caché y la contabilidad viven en
`apps.llm.client`, comunes a todos.
"""

from django.conf import settings

from .base import Completion, Provider


def get_provider(name: str | None = None) -> Provider:
    name = name or settings.LLM_PROVIDER
    if name == "nvidia":
        from .nvidia import NvidiaProvider

        return NvidiaProvider()
    if name == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(f"LLM_PROVIDER desconocido: {name!r} (usa 'nvidia' o 'anthropic')")


__all__ = ["Completion", "Provider", "get_provider"]
