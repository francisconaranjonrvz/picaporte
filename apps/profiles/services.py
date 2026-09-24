"""Casos de uso del perfil: subir el CV, parsearlo con IA y lanzar la búsqueda personalizada."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.catalog.models import Category
from apps.companies.relevance import searched_sectors
from apps.enrichment.profile import profile_fingerprint, profile_is_usable
from apps.jobs.models import JobRun
from apps.jobs.services import start_from_app
from apps.llm.client import LLMResult, call_structured
from apps.llm.models import LLMCall
from apps.llm.prompts import load_prompt

from .models import MAX_CV_BYTES, CVDocument, Profile
from .schemas import ParsedCV

CV_PROMPT = "cv_parse_v2"
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
    version, system = load_prompt(CV_PROMPT)
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

    Los sectores que propone la IA se aplican igual: solo si el perfil no tenía ninguno
    (o con `overwrite`). Devuelve los nombres de los campos que han cambiado.
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
    suggested = list(Category.objects.filter(slug__in=parsed["sectors"], is_active=True))
    current_sectors = set(profile.categories.values_list("pk", flat=True))
    wanted = {c.pk for c in suggested}
    if suggested and (overwrite or not current_sectors) and wanted != current_sectors:
        profile.categories.set(suggested)
        changed.append("categories")
    profile.parsed_at = timezone.now()
    profile.parsed_model = result.call.model
    profile.parsed_prompt_version = result.call.prompt_version
    profile.save()
    return changed


# --- Búsqueda personalizada ------------------------------------------------------------------


def fingerprint_or_none(profile: Profile) -> str | None:
    return profile_fingerprint(profile) if profile_is_usable(profile) else None


def personal_search_needed(profile: Profile, fingerprint_before: str | None) -> str | None:
    """Qué hay que rehacer tras guardar el perfil: "discover" (sectores nuevos: buscar y
    puntuar), "score" (mismo sector, perfil distinto: puntuar) o None."""
    if not profile_is_usable(profile):
        return None
    if sorted(searched_sectors(profile)) != sorted(profile.discovered_sectors):
        return "discover"
    if profile_fingerprint(profile) != fingerprint_before:
        return "score"
    return None


def launch_personal_search(*, discover: bool) -> JobRun:
    """Lanza personalize.yml (o devuelve el que ya esté en marcha)."""
    return start_from_app(JobRun.Kind.PERSONALIZE, {"discover": "1" if discover else "0"})
