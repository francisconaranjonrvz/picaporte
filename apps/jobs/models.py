"""Ejecuciones de trabajos pesados (descubrimiento, enriquecimiento...) en GitHub Actions."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class JobRun(models.Model):
    class Kind(models.TextChoices):
        DISCOVER = "discover", "Descubrimiento"
        ENRICH = "enrich", "Enriquecimiento"
        PERSONALIZE = "personalize", "Búsqueda personalizada"

    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "En marcha"
        SUCCESS = "success", "Terminado"
        FAILED = "failed", "Fallido"

    class Trigger(models.TextChoices):
        SCHEDULE = "schedule", "Programado"
        MANUAL = "manual", "Manual (línea de comandos)"
        APP = "app", "Desde la app"

    kind = models.CharField("tipo", max_length=16, choices=Kind.choices)
    # Solo en la búsqueda personalizada: de quién es. Descubrir y enriquecer son globales.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="job_runs",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        "estado", max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    trigger = models.CharField(
        "origen", max_length=16, choices=Trigger.choices, default=Trigger.MANUAL
    )
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField("métricas", default=dict, blank=True)
    cost_usd = models.DecimalField(
        "coste estimado (USD)", max_digits=10, decimal_places=4, default=0
    )
    summary = models.TextField("resumen", blank=True)
    error = models.TextField(blank=True)
    github_run_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    github_run_url = models.URLField(max_length=300, blank=True)
    github_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ejecución"
        verbose_name_plural = "ejecuciones"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.get_status_display()} · {self.created_at:%d/%m %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)

    @property
    def duration_seconds(self) -> int | None:
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at).total_seconds())
        return None

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_finished(self, *, stats: dict, summary: str, error: str = "") -> None:
        self.status = self.Status.FAILED if error else self.Status.SUCCESS
        self.finished_at = timezone.now()
        self.stats = stats
        self.summary = summary
        self.error = error
        self.save(update_fields=["status", "finished_at", "stats", "summary", "error"])
