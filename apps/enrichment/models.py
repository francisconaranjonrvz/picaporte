"""Enriquecimiento de empresas: páginas rastreadas, datos extraídos por IA y encaje con el perfil.

Dos etapas independientes, cada una con su versión de prompt:

1. **Extracción** (depende solo de la web): servicios, clientes, tamaño, catalán,
   resumen… Se rehace únicamente si cambian las páginas (`pages_hash`).
2. **Puntuación** (perfil + datos extraídos): `fit_score`, justificación y gancho.
   Al cambiar el perfil solo se repite esta etapa (`profile_hash`), sin rastrear
   ni extraer de nuevo.
"""

from django.db import models
from django.utils import timezone

from apps.companies.models import Company


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

    # Puntuación (IA, perfil + empresa)
    fit_score = models.PositiveSmallIntegerField("encaje (0-100)", null=True, blank=True)
    fit_reason = models.TextField("justificación", blank=True)
    hook = models.TextField("gancho para presentarse", blank=True)
    scored_at = models.DateTimeField(null=True, blank=True)
    profile_hash = models.CharField(max_length=64, blank=True)
    scoring_model = models.CharField(max_length=64, blank=True)
    scoring_prompt_version = models.CharField(max_length=64, blank=True)

    error = models.CharField("último error de IA", max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "enriquecimiento"
        verbose_name_plural = "enriquecimientos"
        indexes = [models.Index(fields=["-fit_score"])]

    def __str__(self) -> str:
        return f"{self.company} · {self.fit_score if self.fit_score is not None else '—'}"

    @property
    def needs_extraction(self) -> bool:
        return self.crawl_status == self.CrawlStatus.OK and (
            self.extracted_pages_hash != self.pages_hash
        )
