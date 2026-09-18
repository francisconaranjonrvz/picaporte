"""Registro y caché de llamadas a la Anthropic API.

Cada llamada queda guardada con su hash de entrada: repetir la misma
petición (mismo modelo, misma versión de prompt, misma entrada) devuelve la
respuesta guardada sin gastar tokens. Los contadores alimentan la tabla de
costes del README.
"""

from django.db import models


class LLMCall(models.Model):
    class Purpose(models.TextChoices):
        CV_PARSE = "cv_parse", "Parseo de CV"
        ENRICHMENT = "enrichment", "Enriquecimiento de empresa"
        SCORING = "scoring", "Scoring de encaje"

    purpose = models.CharField("propósito", max_length=32, choices=Purpose.choices)
    model = models.CharField("modelo", max_length=64)
    prompt_version = models.CharField("versión del prompt", max_length=64)
    input_hash = models.CharField("hash de entrada", max_length=64, unique=True)
    response = models.JSONField("respuesta validada")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField("coste (USD)", max_digits=10, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "llamada LLM"
        verbose_name_plural = "llamadas LLM"

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} · {self.model} · {self.created_at:%Y-%m-%d %H:%M}"
