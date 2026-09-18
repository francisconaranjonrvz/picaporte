"""Catálogo configurable en BD: categorías de empresa y zonas de Barcelona.

Lo usan las preferencias del perfil (fase 2) y el descubrimiento (fase 3).
Los valores iniciales se siembran en la migración 0002.
"""

from django.db import models


class Category(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField("nombre", max_length=80)
    order = models.PositiveSmallIntegerField("orden", default=0)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "categoría"
        verbose_name_plural = "categorías"

    def __str__(self) -> str:
        return self.name


class Zone(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField("nombre", max_length=80)
    # Caja delimitadora [lng_min, lat_min, lng_max, lat_max] (barrido de fuentes en fase 3).
    bbox = models.JSONField(default=list, blank=True)
    order = models.PositiveSmallIntegerField("orden", default=0)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "zona"
        verbose_name_plural = "zonas"

    def __str__(self) -> str:
        return self.name
