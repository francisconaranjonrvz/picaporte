"""Perfil de la usuaria: CV en PDF, datos parseados por IA y preferencias de búsqueda."""

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.catalog.models import Category, Zone

MAX_CV_BYTES = 4 * 1024 * 1024  # Vercel limita el body de la request a 4,5 MB


class CompanySize(models.TextChoices):
    MICRO = "micro", "Micro (1-9)"
    SMALL = "pequena", "Pequeña (10-49)"
    MEDIUM = "mediana", "Mediana (50-249)"
    LARGE = "grande", "Grande (250+)"


class WorkLanguage(models.TextChoices):
    ES = "es", "Español"
    CA = "ca", "Catalán"
    EN = "en", "Inglés"


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )

    # Datos parseados del CV (editables a mano).
    full_name = models.CharField("nombre completo", max_length=120, blank=True)
    headline = models.CharField("titular", max_length=120, blank=True)
    summary = models.TextField("resumen", blank=True)
    education = models.JSONField(
        "formación", default=list, blank=True
    )  # [{title, organization, period}]
    experience = models.JSONField(
        "experiencia", default=list, blank=True
    )  # [{title, organization, period, summary}]
    skills = models.JSONField("habilidades", default=list, blank=True)  # [str]
    languages = models.JSONField("idiomas", default=list, blank=True)  # [{language, level}]

    # Preferencias de búsqueda.
    categories = models.ManyToManyField(Category, verbose_name="categorías", blank=True)
    zones = models.ManyToManyField(Zone, verbose_name="zonas", blank=True)
    company_sizes = models.JSONField("tamaño de empresa", default=list, blank=True)  # [CompanySize]
    work_languages = models.JSONField(
        "idiomas de trabajo", default=list, blank=True
    )  # [WorkLanguage]
    interests = models.TextField("intereses", blank=True)

    # Trazabilidad del último parseo.
    parsed_at = models.DateTimeField(null=True, blank=True)
    parsed_model = models.CharField(max_length=64, blank=True)
    parsed_prompt_version = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "perfil"
        verbose_name_plural = "perfiles"

    def __str__(self) -> str:
        return self.full_name or self.user.get_username()

    @property
    def has_parsed_data(self) -> bool:
        return bool(
            self.full_name or self.summary or self.skills or self.experience or self.education
        )

    @property
    def is_complete(self) -> bool:
        return self.has_parsed_data and self.categories.exists() and self.zones.exists()


class CVDocument(models.Model):
    """El PDF del CV guardado en la base de datos (bytea). Solo se conserva el actual."""

    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="cv")
    filename = models.CharField("nombre de archivo", max_length=255)
    size = models.PositiveIntegerField("tamaño (bytes)")
    sha256 = models.CharField(max_length=64)
    data = models.BinaryField(editable=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "CV"
        verbose_name_plural = "CVs"

    def __str__(self) -> str:
        return self.filename

    @classmethod
    def from_upload(cls, profile: Profile, filename: str, data: bytes) -> "CVDocument":
        """Crea o sustituye el CV del perfil (sin cargar el PDF anterior)."""
        digest = hashlib.sha256(data).hexdigest()
        cv, _ = cls.objects.update_or_create(
            profile=profile,
            defaults={
                "filename": filename[:255],
                "size": len(data),
                "sha256": digest,
                "data": data,
                "uploaded_at": timezone.now(),  # auto_now_add no se dispara al sustituir
            },
        )
        return cv

    @property
    def size_label(self) -> str:
        kb = self.size / 1024
        return f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
