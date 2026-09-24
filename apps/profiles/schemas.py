"""Esquema Pydantic de la salida del parseo de CV (structured outputs).

Todos los campos (salvo `sectors`, añadido en v2) son obligatorios a propósito:
el modelo devuelve cadenas/listas vacías cuando un dato no está.
"""

from pydantic import BaseModel, Field, field_validator

from apps.companies.relevance import ALL_SECTORS


class EducationItem(BaseModel):
    title: str = Field(description="Nombre de la titulación o curso")
    organization: str = Field(description="Centro o institución")
    period: str = Field(description="Periodo, p. ej. '2020-2024'")


class ExperienceItem(BaseModel):
    title: str = Field(description="Puesto o rol")
    organization: str = Field(description="Empresa u organización")
    period: str = Field(description="Periodo, p. ej. 'jun 2024 - sep 2024'")
    summary: str = Field(description="Una frase con responsabilidades o logros")


class LanguageItem(BaseModel):
    language: str = Field(description="Idioma")
    level: str = Field(description="Nivel tal como aparece en el CV")


class ParsedCV(BaseModel):
    full_name: str
    headline: str
    summary: str
    education: list[EducationItem]
    experience: list[ExperienceItem]
    skills: list[str]
    languages: list[LanguageItem]
    sectors: list[str] = Field(
        default_factory=list,
        description="Slugs de los sectores donde encaja, de la lista del prompt (máx. 6)",
    )

    @field_validator("sectors")
    @classmethod
    def known_sectors(cls, value: list[str]) -> list[str]:
        """Se ignoran los slugs inventados en vez de invalidar todo el parseo."""
        return [s for s in dict.fromkeys(value) if s in ALL_SECTORS][:6]
