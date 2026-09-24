"""Salidas Pydantic de las dos etapas de IA (extracción y puntuación).

Los campos son obligatorios: el modelo devuelve cadenas o listas vacías, o
"desconocido", cuando la web no dice nada. Los excesos se recortan en lugar
de fallar (un resumen de 700 caracteres no justifica repetir la llamada), y
así nada enorme llega a la BD.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

from .ranking import clamp_breakdown


def _truncate(limit: int):
    return BeforeValidator(lambda v: v[:limit] if isinstance(v, str | list) else v)


def _points(value) -> int:
    """Puntos de un criterio: tolera "30", 30.0 o basura (-> 0); el rango lo fija el ranking."""
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return 0


Points = Annotated[int, BeforeValidator(_points)]


def _language(value):
    code = str(value).lower()[:2]
    return code if code in {"es", "ca", "en"} else "otro"


Summary = Annotated[str, _truncate(600)]
Sentence = Annotated[str, _truncate(400)]
Language = Annotated[Literal["es", "ca", "en", "otro"], BeforeValidator(_language)]


class ExtractedCompany(BaseModel):
    is_company_site: bool = Field(
        description="false si la web no es de esta empresa (dominio aparcado, directorio, otra empresa)"
    )
    summary: Summary = Field(description="Qué hace la empresa, en 3 líneas como máximo, en español")
    services: Annotated[list[Annotated[str, _truncate(80)]], _truncate(8)] = Field(
        description="Servicios principales (máx. 8), frases cortas"
    )
    clients: Annotated[list[Annotated[str, _truncate(80)]], _truncate(10)] = Field(
        description="Clientes, marcas o proyectos citados en la web (máx. 10)"
    )
    size_estimate: Literal["micro", "pequena", "mediana", "grande", "desconocido"]
    requires_catalan: Literal["si", "no", "desconocido"]
    site_languages: Annotated[list[Language], _truncate(4)]
    jobs_url: str = Field(
        description="URL de la página de empleo o prácticas si aparece entre las páginas; si no, ''"
    )
    hiring_note: Annotated[str, _truncate(300)] = Field(
        description="Una frase si la web menciona ofertas, prácticas o candidaturas espontáneas; si no, ''"
    )


class CompanyScore(BaseModel):
    """Nota por criterios (ver `ranking.DIMENSIONS`); la total la suma el código."""

    company_id: int
    sector: Points = Field(description="Sector y servicios afines, 0-40")
    junior: Points = Field(description="Hueco para un perfil junior, 0-20")
    preferences: Points = Field(description="Preferencias declaradas (sector, zona, tamaño), 0-20")
    languages: Points = Field(description="Idiomas, 0-10")
    clarity: Points = Field(description="Claridad de la información disponible, 0-10")
    reason: Sentence = Field(description="Justificación en 1-2 frases, en español")
    hook: Sentence = Field(
        description="Dos frases en primera persona para presentarse en persona en esa empresa"
    )

    @property
    def breakdown(self) -> dict[str, int]:
        return clamp_breakdown(
            {
                k: getattr(self, k)
                for k in ("sector", "junior", "preferences", "languages", "clarity")
            }
        )

    @property
    def fit_score(self) -> int:
        return sum(self.breakdown.values())


class ScoreBatch(BaseModel):
    scores: list[CompanyScore]
