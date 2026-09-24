"""Lo que la web necesita del enriquecimiento, sin dependencias del worker.

Vercel no instala el grupo `worker` (BeautifulSoup, DuckDB): este módulo no
debe importar `crawler` ni `services`.
"""

import hashlib

from apps.profiles.models import CompanySize, Profile, WorkLanguage

from .models import Enrichment

SCORE_PROMPT = "enrich_score_v2"


def current_profile() -> Profile | None:
    return Profile.objects.order_by("pk").prefetch_related("categories", "zones").first()


def profile_brief(profile: Profile) -> str:
    """El perfil en texto para el prompt de puntuación (solo lo relevante para el encaje)."""
    sizes = dict(CompanySize.choices)
    languages = dict(WorkLanguage.choices)
    lines = [
        f"Titular: {profile.headline}",
        f"Resumen: {profile.summary}",
        "Formación: "
        + "; ".join(f"{e.get('title')} ({e.get('organization')})" for e in profile.education),
        "Experiencia: "
        + "; ".join(
            f"{e.get('title')} en {e.get('organization')}: {e.get('summary')}"
            for e in profile.experience
        ),
        "Habilidades: " + ", ".join(profile.skills),
        "Idiomas: "
        + ", ".join(f"{i.get('language')} ({i.get('level')})" for i in profile.languages),
        "Sectores que le interesan: " + ", ".join(c.name for c in profile.categories.all()),
        "Zonas preferidas: " + ", ".join(z.name for z in profile.zones.all()),
        "Tamaño de empresa preferido: " + ", ".join(sizes.get(s, s) for s in profile.company_sizes),
        "Idiomas en los que quiere trabajar: "
        + ", ".join(languages.get(code, code) for code in profile.work_languages),
        f"Intereses: {profile.interests}",
    ]
    return "\n".join(lines)


def profile_fingerprint(profile: Profile) -> str:
    """Cambia cuando cambia algo que afecta a la puntuación (y la versión del prompt)."""
    return hashlib.sha256(f"{SCORE_PROMPT}\n{profile_brief(profile)}".encode()).hexdigest()


def profile_is_usable(profile: Profile | None) -> bool:
    return profile is not None and profile.has_parsed_data


def stale_scores_count(profile: Profile | None = None) -> int:
    """Empresas cuya puntuación no corresponde al perfil actual (para el aviso de la UI)."""
    profile = profile or current_profile()
    if not profile_is_usable(profile):
        return 0
    fingerprint = profile_fingerprint(profile)
    return (
        Enrichment.objects.filter(company__is_active=True, scored_at__isnull=False)
        .exclude(profile_hash=fingerprint)
        .count()
    )
