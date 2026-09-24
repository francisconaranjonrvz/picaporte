"""Empresas descubiertas, sus registros por fuente y la caché de respuestas HTTP."""

from urllib.parse import quote_plus

from django.db import models
from django.utils import timezone

from apps.catalog.models import Category, Zone


class Source(models.TextChoices):
    OSM = "osm", "OpenStreetMap"
    FOURSQUARE = "foursquare", "Foursquare OS Places"
    OPENDATA_BCN = "opendata_bcn", "Cens de locals (Ajuntament de Barcelona)"
    MANUAL = "manual", "Manual"


# Al fusionar, gana la fuente con mayor prioridad para cada campo.
SOURCE_PRIORITY: dict[str, int] = {
    Source.MANUAL: 100,
    Source.FOURSQUARE: 60,
    Source.OSM: 50,
    # Dirección municipal fiable pero nombres en mayúsculas y categoría inferida del nombre.
    Source.OPENDATA_BCN: 40,
}


class Company(models.Model):
    name = models.CharField("nombre", max_length=200)
    category = models.ForeignKey(
        Category, verbose_name="categoría", null=True, blank=True, on_delete=models.SET_NULL
    )
    zone = models.ForeignKey(
        Zone, verbose_name="zona", null=True, blank=True, on_delete=models.SET_NULL
    )

    address = models.CharField("dirección", max_length=255, blank=True)
    postcode = models.CharField("código postal", max_length=10, blank=True)
    city = models.CharField("ciudad", max_length=80, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)

    website = models.URLField("web", max_length=300, blank=True)
    domain = models.CharField("dominio", max_length=120, blank=True, db_index=True)
    phone = models.CharField("teléfono", max_length=40, blank=True)
    email = models.EmailField(blank=True)
    opening_hours = models.CharField("horario (OSM opening_hours)", max_length=255, blank=True)
    rating = models.FloatField(null=True, blank=True)
    rating_count = models.PositiveIntegerField(null=True, blank=True)

    # Calidad y procedencia: {campo: fuente} para saber de dónde salió cada dato.
    confidence_score = models.PositiveSmallIntegerField("confianza", default=0)
    field_sources = models.JSONField(default=dict, blank=True)

    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        ordering = ["-confidence_score", "name"]
        verbose_name = "empresa"
        verbose_name_plural = "empresas"
        indexes = [models.Index(fields=["lat", "lng"])]

    def __str__(self) -> str:
        return self.name

    # Datos del usuario que mira la empresa, precargados por `personal.for_user`/`attach`.
    @property
    def score(self):
        return _first(self, "my_scores")

    @property
    def visit(self):
        return _first(self, "my_visits")

    @property
    def favorite(self):
        return _first(self, "my_favorites")

    @property
    def source_names(self) -> list[str]:
        return list(self.records.values_list("source", flat=True))

    @property
    def maps_url(self) -> str:
        """Enlace de Google Maps: por coordenadas si las hay; si no, por nombre y dirección."""
        if self.lat is not None and self.lng is not None:
            return f"https://www.google.com/maps/search/?api=1&query={self.lat},{self.lng}"
        if self.address:
            query = quote_plus(f"{self.name}, {self.address}, Barcelona")
            return f"https://www.google.com/maps/search/?api=1&query={query}"
        return ""

    @property
    def tel_url(self) -> str:
        digits = "".join(ch for ch in self.phone if ch.isdigit() or ch == "+")
        return f"tel:{digits}" if digits else ""


def _first(company: Company, attr: str):
    rows = getattr(company, attr, None)
    return rows[0] if rows else None


class SourceRecord(models.Model):
    """Lo que dijo cada fuente sobre una empresa, tal cual, para poder re-fusionar."""

    company = models.ForeignKey(Company, related_name="records", on_delete=models.CASCADE)
    source = models.CharField("fuente", max_length=32, choices=Source.choices)
    external_id = models.CharField("id externo", max_length=120)
    name = models.CharField(max_length=200)
    payload = models.JSONField("datos crudos", default=dict, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_record")
        ]
        verbose_name = "registro de fuente"
        verbose_name_plural = "registros de fuente"

    def __str__(self) -> str:
        return f"{self.get_source_display()} · {self.external_id}"


class FetchCache(models.Model):
    """Respuestas HTTP cacheadas (APIs y fetches web) para no repetir coste ni carga."""

    key = models.CharField(max_length=64, unique=True)  # sha256(método + url + cuerpo)
    url = models.CharField(max_length=500)
    status_code = models.PositiveSmallIntegerField()
    body = models.TextField()
    fetched_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "respuesta cacheada"
        verbose_name_plural = "respuestas cacheadas"

    def __str__(self) -> str:
        return f"{self.status_code} {self.url[:80]}"

    @property
    def is_fresh(self) -> bool:
        return self.expires_at > timezone.now()
