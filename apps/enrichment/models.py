"""Enriquecimiento de empresas: páginas rastreadas, datos extraídos por IA y encaje con el perfil.

Dos etapas independientes, cada una con su versión de prompt:

1. **Extracción** (depende solo de la web): servicios, clientes, tamaño, catalán,
   resumen… Se rehace únicamente si cambian las páginas (`pages_hash`).
2. **Puntuación** (perfil de cada usuario + datos extraídos): `FitScore`, con nota,
   desglose, justificación y gancho. Al cambiar un perfil solo se repite esta etapa
   para ese usuario (`profile_hash`), sin rastrear ni extraer de nuevo.

El rastreo y la extracción son compartidos (la web es la misma para todos); la
puntuación es de cada usuario.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.companies.models import Company

# Extracciones fallidas seguidas con las mismas páginas antes de rendirse hasta el re-rastreo.
MAX_EXTRACTION_ATTEMPTS = 3


class CompanyPage(models.Model):
    """Texto visible de una página de la web de la empresa (la caché del rastreo)."""

    class Kind(models.TextChoices):
        HOME = "home", "Inicio"
        ABOUT = "about", "Sobre nosotros"
        TEAM = "team", "Equipo"
        JOBS = "jobs", "Empleo"
        CONTACT = "contact", "Contacto"

    company = models.ForeignKey(Company, related_name="pages", on_delete=models.CASCADE)
    url = models.URLField(max_length=500)
    kind = models.CharField("tipo", max_length=16, choices=Kind.choices)
    status_code = models.PositiveSmallIntegerField()
    lang = models.CharField("idioma declarado", max_length=12, blank=True)
    text = models.TextField("texto visible")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "url"], name="unique_company_page")
        ]
        verbose_name = "página"
        verbose_name_plural = "páginas"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.url}"


class Enrichment(models.Model):
    class CrawlStatus(models.TextChoices):
        PENDING = "pending", "Pendiente"
        OK = "ok", "Rastreada"
        NO_WEBSITE = "no_website", "Sin web"
        NOT_A_SITE = "not_a_site", "Solo redes sociales"
        ROBOTS = "robots", "robots.txt no lo permite"
        UNREACHABLE = "unreachable", "Web caída o inaccesible"
        NOT_HTML = "not_html", "La web no es HTML"

    class Size(models.TextChoices):
        MICRO = "micro", "Micro (1-9)"
        SMALL = "pequena", "Pequeña (10-49)"
        MEDIUM = "mediana", "Mediana (50-249)"
        LARGE = "grande", "Grande (250+)"
        UNKNOWN = "desconocido", "Desconocido"

    class Catalan(models.TextChoices):
        YES = "si", "Sí"
        NO = "no", "No"
        UNKNOWN = "desconocido", "Desconocido"

    company = models.OneToOneField(Company, related_name="enrichment", on_delete=models.CASCADE)

    # Rastreo
    crawl_status = models.CharField(
        "estado del rastreo",
        max_length=16,
        choices=CrawlStatus.choices,
        default=CrawlStatus.PENDING,
    )
    crawl_error = models.CharField(max_length=300, blank=True)
    crawled_at = models.DateTimeField(null=True, blank=True)
    pages_hash = models.CharField(max_length=64, blank=True)
    emails = models.JSONField(default=list, blank=True)
    socials = models.JSONField("redes sociales", default=dict, blank=True)  # {red: url}

    # Extracción (IA, independiente del perfil)
    is_company_site = models.BooleanField("la web es de la empresa", null=True)
    summary = models.TextField("resumen", blank=True)
    services = models.JSONField("servicios", default=list, blank=True)
    clients = models.JSONField("clientes y proyectos", default=list, blank=True)
    size_estimate = models.CharField(
        "tamaño estimado", max_length=16, choices=Size.choices, default=Size.UNKNOWN
    )
    requires_catalan = models.CharField(
        "¿requiere catalán?", max_length=16, choices=Catalan.choices, default=Catalan.UNKNOWN
    )
    site_languages = models.JSONField("idiomas de la web", default=list, blank=True)
    jobs_url = models.URLField("página de empleo", max_length=500, blank=True)
    hiring_note = models.CharField("señales de contratación", max_length=300, blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)
    extracted_pages_hash = models.CharField(max_length=64, blank=True)
    extraction_model = models.CharField(max_length=64, blank=True)
    extraction_prompt_version = models.CharField(max_length=64, blank=True)
    extraction_attempts = models.PositiveSmallIntegerField(
        "extracciones fallidas seguidas", default=0
    )

    error = models.CharField("último error de IA", max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "enriquecimiento"
        verbose_name_plural = "enriquecimientos"

    def __str__(self) -> str:
        return f"{self.company} · {self.get_crawl_status_display()}"

    @property
    def needs_extraction(self) -> bool:
        """Páginas nuevas sin analizar. Tras `MAX_EXTRACTION_ATTEMPTS` fallos se deja de
        intentar (y se puntúa sin datos extraídos) hasta el próximo rastreo."""
        return (
            self.crawl_status == self.CrawlStatus.OK
            and self.extracted_pages_hash != self.pages_hash
            and self.extraction_attempts < MAX_EXTRACTION_ATTEMPTS
        )


class FitScore(models.Model):
    """Encaje de una empresa con el perfil de un usuario (ranking personal)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="fit_scores", on_delete=models.CASCADE
    )
    company = models.ForeignKey(Company, related_name="fit_scores", on_delete=models.CASCADE)
    fit_score = models.PositiveSmallIntegerField("encaje (0-100)")
    fit_breakdown = models.JSONField("encaje por criterios", default=dict, blank=True)
    fit_reason = models.TextField("justificación", blank=True)
    hook = models.TextField("gancho para presentarse", blank=True)
    scored_at = models.DateTimeField(default=timezone.now)
    profile_hash = models.CharField(max_length=64, blank=True)
    scoring_model = models.CharField(max_length=64, blank=True)
    scoring_prompt_version = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "company"], name="unique_user_fit_score")
        ]
        indexes = [models.Index(fields=["user", "-fit_score"])]
        verbose_name = "encaje"
        verbose_name_plural = "encajes"

    def __str__(self) -> str:
        return f"{self.company} · {self.fit_score} ({self.user})"

    @property
    def verdict(self):
        from .ranking import verdict

        return verdict(self.fit_score)

    @property
    def breakdown_rows(self) -> list[dict]:
        from .ranking import breakdown_rows

        return breakdown_rows(self.fit_breakdown)
