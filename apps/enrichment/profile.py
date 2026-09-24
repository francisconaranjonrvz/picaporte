"""Lo que la web necesita del enriquecimiento, sin dependencias del worker.

Vercel no instala el grupo `worker` (BeautifulSoup, DuckDB): este módulo no
debe importar `crawler` ni `services`.
"""

import hashlib

from apps.profiles.models import CompanySize, Profile, WorkLanguage

from .models import FitScore

SCORE_PROMPT = "enrich_score_v2"


def profile_for(user) -> Profile | None:
    return (
        Profile.objects.filter(user=user).prefetch_related("categories", "zones").first()
        if user is not None and user.is_authenticated
        else None
    )


def usable_profiles() -> list[Profile]:
    """Perfiles con datos suficientes para puntuar (los de todas las cuentas)."""
    profiles = Profile.objects.select_related("user").prefetch_related("categories", "zones")
    return [p for p in profiles.order_by("pk") if profile_is_usable(p)]


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


def stale_scores_count(profile: Profile | None) -> int:
    """Empresas puntuadas con una versión anterior de este perfil (para el aviso de la UI)."""
    if not profile_is_usable(profile):
        return 0
    return (
        FitScore.objects.filter(user_id=profile.user_id, company__is_active=True)
        .exclude(profile_hash=profile_fingerprint(profile))
        .count()
    )
