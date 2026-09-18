"""Casos de uso del perfil: subir el CV y parsearlo con IA."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.llm.client import LLMResult, call_structured
from apps.llm.models import LLMCall
from apps.llm.prompts import load_prompt

from .models import MAX_CV_BYTES, CVDocument, Profile
from .schemas import ParsedCV

PDF_MAGIC = b"%PDF-"
PARSED_FIELDS = (
    "full_name",
    "headline",
    "summary",
    "education",
    "experience",
    "skills",
    "languages",
)


def validate_pdf(filename: str, data: bytes) -> None:
    """Comprueba tamaño y cabecera; el content-type del navegador no es fiable."""
    if not data:
        raise ValidationError("El archivo está vacío.")
    if len(data) > MAX_CV_BYTES:
        raise ValidationError(f"El CV supera el límite de {MAX_CV_BYTES // (1024 * 1024)} MB.")
    if not data.startswith(PDF_MAGIC):
        raise ValidationError("El archivo no es un PDF válido.")
    if not filename.lower().endswith(".pdf"):
        raise ValidationError("El archivo debe tener extensión .pdf.")


def save_cv(profile: Profile, filename: str, data: bytes) -> CVDocument:
    validate_pdf(filename, data)
    return CVDocument.from_upload(profile, filename, data)


def parse_cv(cv: CVDocument) -> LLMResult[ParsedCV]:
    """Envía el PDF a la IA (o devuelve la caché) y valida la salida."""
    version, system = load_prompt("cv_parse_v1")
    return call_structured(
        purpose=LLMCall.Purpose.CV_PARSE,
        prompt_version=version,
        system=system,
        user_text="Extrae la información de este currículum.",
        output_model=ParsedCV,
        pdf_bytes=bytes(cv.data),
    )


def apply_parsed(
    profile: Profile, result: LLMResult[ParsedCV], *, overwrite: bool = False
) -> list[str]:
    """Vuelca el parseo en el perfil. Sin `overwrite` solo rellena campos vacíos.

    Devuelve los nombres de los campos que han cambiado.
    """
    parsed = result.output.model_dump(mode="json")
    changed: list[str] = []
    for field in PARSED_FIELDS:
        new_value = parsed[field]
        current = getattr(profile, field)
        if not new_value or (current and not overwrite):
            continue
        if current != new_value:
            setattr(profile, field, new_value)
            changed.append(field)
    profile.parsed_at = timezone.now()
    profile.parsed_model = result.call.model
    profile.parsed_prompt_version = result.call.prompt_version
    profile.save()
    return changed
