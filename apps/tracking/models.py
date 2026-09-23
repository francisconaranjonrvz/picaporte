"""Seguimiento de la búsqueda: favoritas, estado de cada empresa y notas.

- `Favorite`: la lista corta, con prioridad y orden manual (arrastrar y soltar).
- `Visit`: el estado actual de cada empresa (una fila por empresa, creada al tocarla),
  con fecha, persona de contacto y la próxima acción con su fecha.
- `Note`: historial en orden cronológico; cada cambio de estado deja también una nota.
"""

from django.db import models
from django.utils import timezone

from apps.companies.models import Company


class Favorite(models.Model):
    class Priority(models.TextChoices):
        HIGH = "alta", "Alta"
        MEDIUM = "media", "Media"
        LOW = "baja", "Baja"

    company = models.OneToOneField(Company, related_name="favorite", on_delete=models.CASCADE)
    priority = models.CharField(
        "prioridad", max_length=8, choices=Priority.choices, default=Priority.MEDIUM
    )
    position = models.PositiveIntegerField("orden", default=0, db_index=True)
    note = models.CharField("nota", max_length=280, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["position", "-created_at", "-id"]
        verbose_name = "favorita"
        verbose_name_plural = "favoritas"

    def __str__(self) -> str:
        return f"{self.company} ({self.get_priority_display()})"


class Visit(models.Model):
    class Status(models.TextChoices):
        PENDING = "pendiente", "Pendiente"
        PLANNED = "planificado", "Planificado"
        VISITED = "visitado", "Visitado"
        CV_DELIVERED = "cv_entregado", "CV entregado"
        RETURN = "volver", "Volver"
        DISCARDED = "descartado", "Descartado"

    # Clase del badge de cada estado (design system, /styleguide).
    BADGES = {
        Status.PENDING: "badge-muted",
        Status.PLANNED: "badge-primary",
        Status.VISITED: "badge-success",
        Status.CV_DELIVERED: "badge-success",
        Status.RETURN: "badge-warning",
        Status.DISCARDED: "badge-danger",
    }

    company = models.OneToOneField(Company, related_name="visit", on_delete=models.CASCADE)
    status = models.CharField(
        "estado", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    visited_on = models.DateField("fecha de la visita", null=True, blank=True)
    contact = models.CharField("persona de contacto", max_length=120, blank=True)
    next_action = models.CharField("próxima acción", max_length=200, blank=True)
    next_action_on = models.DateField("fecha de la próxima acción", null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "visita"
        verbose_name_plural = "visitas"

    def __str__(self) -> str:
        return f"{self.company} · {self.get_status_display()}"

    @property
    def badge(self) -> str:
        return self.BADGES.get(self.status, "badge-muted")


class Note(models.Model):
    company = models.ForeignKey(Company, related_name="notes", on_delete=models.CASCADE)
    text = models.TextField("nota")
    is_status_change = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]  # desempate: dos notas en el mismo instante
        verbose_name = "nota"
        verbose_name_plural = "notas"

    def __str__(self) -> str:
        return self.text[:60]
