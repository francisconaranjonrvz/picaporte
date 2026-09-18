"""Esquema Pydantic de la salida del parseo de CV (structured outputs).

Todos los campos son obligatorios a propósito: la API garantiza el JSON
completo y el modelo devuelve cadenas/listas vacías cuando un dato no está.
"""

from pydantic import BaseModel, Field


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
