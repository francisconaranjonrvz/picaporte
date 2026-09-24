"""Rutas de visita: un día, una zona y una franja, con hasta 20 paradas ordenadas."""

from datetime import time

from django.db import models
from django.utils import timezone

from apps.catalog.models import Zone
from apps.companies.models import Company
from apps.tracking.models import Note


class Route(models.Model):
    class Slot(models.TextChoices):
        MORNING = "manana", "Mañana (9:30-14:00)"
        AFTERNOON = "tarde", "Tarde (15:30-19:00)"
        DAY = "dia", "Todo el día (9:30-19:00)"

    SLOT_TIMES = {
        Slot.MORNING: (time(9, 30), time(14, 0)),
        Slot.AFTERNOON: (time(15, 30), time(19, 0)),
        Slot.DAY: (time(9, 30), time(19, 0)),
    }

    date = models.DateField("día")
    slot = models.CharField("franja", max_length=8, choices=Slot.choices, default=Slot.MORNING)
    zone = models.ForeignKey(
        Zone, verbose_name="zona", null=True, blank=True, on_delete=models.SET_NULL
    )
    start_lat = models.FloatField(null=True, blank=True)
    start_lng = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "ruta"
        verbose_name_plural = "rutas"

    def __str__(self) -> str:
        zone = self.zone.name if self.zone else "Barcelona"
        return f"{self.date:%d/%m} · {zone} · {self.get_slot_display()}"

    @property
    def times(self) -> tuple[time, time]:
        return self.SLOT_TIMES[self.slot]


class RouteStop(models.Model):
    class State(models.TextChoices):
        PENDING = "pendiente", "Pendiente"
        DELIVERED = "entregado", "CV entregado"
        VISITED = "visitada", "Visitada"
        CLOSED = "cerrada", "Cerrada"
        SKIPPED = "saltada", "Saltada"

    route = models.ForeignKey(Route, related_name="stops", on_delete=models.CASCADE)
    company = models.ForeignKey(Company, related_name="route_stops", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField("orden")
    state = models.CharField("estado", max_length=12, choices=State.choices, default=State.PENDING)
    distance_m = models.PositiveIntegerField("distancia desde la parada anterior (m)", default=0)
    # Estado de seguimiento de la empresa antes de marcar la parada, para poder deshacer.
    previous_status = models.CharField(max_length=16, blank=True)
    # Nota "Cerrada al pasar…" que dejó la marca, para borrarla al deshacer.
    closed_note = models.ForeignKey(
        Note, null=True, blank=True, related_name="+", on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["route", "company"], name="unique_route_company")
        ]
        verbose_name = "parada"
        verbose_name_plural = "paradas"

    def __str__(self) -> str:
        return f"{self.position}. {self.company}"

    @property
    def is_done(self) -> bool:
        return self.state != self.State.PENDING
