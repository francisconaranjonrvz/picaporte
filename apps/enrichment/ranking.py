"""Ranking de encaje, a la manera de career-ops: criterios con peso fijo y un veredicto.

La IA puntúa cada criterio por separado (`enrich_score_v2`) y la nota final es la
suma, calculada aquí: así los pesos no dependen de que el modelo sume bien y la
ficha puede enseñar de dónde sale cada punto. Módulo apto para la web (sin
dependencias del worker).
"""

from dataclasses import dataclass

# (clave en el JSON de la IA, etiqueta, máximo). Los máximos suman 100.
DIMENSIONS: list[tuple[str, str, int]] = [
    ("sector", "Sector y servicios afines", 40),
    ("junior", "Hueco para un perfil junior", 20),
    ("preferences", "Tus preferencias", 20),
    ("languages", "Idiomas", 10),
    ("clarity", "Información disponible", 10),
]
MAX_BY_KEY = {key: maximum for key, _, maximum in DIMENSIONS}


@dataclass(frozen=True)
class Verdict:
    label: str
    tone: str  # success | warning | muted (clases del design system)
    advice: str


# (nota mínima, veredicto), de mayor a menor; como las bandas 4.5/4.0/3.5 de career-ops.
VERDICTS: list[tuple[int, Verdict]] = [
    (75, Verdict("Ve primero", "success", "Encaja muy bien: prioriza esta visita.")),
    (60, Verdict("Merece la pena", "success", "Buen encaje: inclúyela en tus rutas.")),
    (40, Verdict("Si pasas cerca", "warning", "Encaje parcial: solo si te pilla de paso.")),
    (0, Verdict("Baja prioridad", "muted", "Poco encaje con tu perfil.")),
]


def verdict(score: int | None) -> Verdict | None:
    if score is None:
        return None
    return next(v for minimum, v in VERDICTS if score >= minimum)


def clamp_breakdown(values: dict[str, int]) -> dict[str, int]:
    """Cada criterio dentro de su rango (la IA a veces se pasa); faltan -> 0."""
    return {
        key: max(0, min(maximum, int(values.get(key) or 0))) for key, maximum in MAX_BY_KEY.items()
    }


def breakdown_rows(breakdown: dict) -> list[dict]:
    """Filas para la ficha: etiqueta, puntos, máximo y porcentaje (para la barra)."""
    if not breakdown:
        return []
    rows = []
    for key, label, maximum in DIMENSIONS:
        points = int(breakdown.get(key) or 0)
        rows.append(
            {"label": label, "points": points, "max": maximum, "pct": round(100 * points / maximum)}
        )
    return rows
