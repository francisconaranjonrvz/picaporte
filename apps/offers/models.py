"""Ofertas de empleo importadas (de career-ops u otro origen) y su cruce con las empresas."""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.companies.models import Company

ACTIVE_FOR = timedelta(days=45)  # una oferta vista hace más de 45 días se da por cerrada


class JobOffer(models.Model):
    class Match(models.TextChoices):
        DOMAIN = "domain", "Dominio"
        NAME = "name", "Nombre"
        FUZZY = "fuzzy", "Nombre parecido"
        NONE = "", "Sin empresa"

    title = models.CharField("puesto", max_length=300)
    company_name = models.CharField("empresa (según el portal)", max_length=200)
    portal = models.CharField(max_length=120, blank=True)
    url = models.URLField(max_length=600, unique=True)
    location = models.CharField("ubicación", max_length=200, blank=True)
    published_on = models.DateField("fecha", null=True, blank=True)
    score = models.FloatField("puntuación", null=True, blank=True)
    company = models.ForeignKey(
        Company, related_name="offers", null=True, blank=True, on_delete=models.SET_NULL
    )
    match = models.CharField("cruce", max_length=8, choices=Match.choices, blank=True)
    source = models.CharField("origen", max_length=40, default="career-ops")
    payload = models.JSONField("fila original", default=dict, blank=True)
    imported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-published_on", "-id"]
        verbose_name = "oferta"
        verbose_name_plural = "ofertas"

    def __str__(self) -> str:
        return f"{self.title} · {self.company_name}"

    @property
    def is_active(self) -> bool:
        seen = self.published_on or self.imported_at.date()
        return seen >= timezone.localdate() - ACTIVE_FOR


def active_offers():
    """Ofertas vistas en los últimos 45 días (o sin fecha, importadas en ese plazo)."""
    since = timezone.localdate() - ACTIVE_FOR
    return JobOffer.objects.filter(
        models.Q(published_on__gte=since)
        | models.Q(published_on__isnull=True, imported_at__date__gte=since)
    )
