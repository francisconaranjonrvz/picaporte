"""Registro de fuentes: nombre corto -> adaptador."""

from .base import RawCompany, SourceAdapter, SourceError
from .overpass import OverpassAdapter

ADAPTERS: dict[str, type] = {
    "osm": OverpassAdapter,
}


def get_adapters(names: list[str] | None = None) -> list[SourceAdapter]:
    names = names or list(ADAPTERS)
    unknown = [n for n in names if n not in ADAPTERS]
    if unknown:
        raise ValueError(
            f"Fuentes desconocidas: {', '.join(unknown)} (disponibles: {', '.join(ADAPTERS)})"
        )
    return [ADAPTERS[n]() for n in names]


__all__ = ["ADAPTERS", "RawCompany", "SourceAdapter", "SourceError", "get_adapters"]
