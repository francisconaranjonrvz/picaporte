"""Rastrea las webs de las empresas, las analiza con IA y puntúa su encaje con el perfil.

Pensado para GitHub Actions (enrich.yml); registra un JobRun que la app muestra.

    uv run python manage.py enrich                      # rastrear hasta 150 webs + analizar + puntuar
    uv run python manage.py enrich --mode score         # solo recalcular el encaje (perfil cambiado)
    uv run python manage.py enrich --limit 20 --max-minutes 10
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.enrichment.services import run_enrichment
from apps.jobs.models import JobRun
from apps.jobs.services import begin_job
from apps.llm.models import LLMCall


class Command(BaseCommand):
    help = "Enriquece empresas: rastreo de webs, extracción con IA y puntuación de encaje."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=("all", "score"), default="all")
        parser.add_argument("--limit", type=int, default=150, help="Máximo de webs a rastrear.")
        parser.add_argument(
            "--max-minutes",
            type=float,
            default=40,
            help="Presupuesto de tiempo: al agotarse no se empieza trabajo nuevo.",
        )
        parser.add_argument("--crawl-workers", type=int, default=8)
        parser.add_argument("--llm-workers", type=int, default=3)
        parser.add_argument("--job-id", type=int, default=None, help="JobRun creado por la app.")

    def handle(self, *args, mode, limit, max_minutes, crawl_workers, llm_workers, job_id, **opts):
        job = begin_job(JobRun.Kind.ENRICH, job_id)
        try:
            stats = run_enrichment(
                mode=mode,
                limit=limit,
                max_minutes=max_minutes,
                crawl_workers=crawl_workers,
                llm_workers=llm_workers,
            )
        except Exception as exc:
            job.mark_finished(stats={}, summary="", error=f"{type(exc).__name__}: {exc}")
            raise

        job.cost_usd = LLMCall.objects.filter(created_at__gte=job.started_at).aggregate(
            total=Sum("cost_usd")
        )["total"] or Decimal(0)
        job.save(update_fields=["cost_usd"])
        summary = stats.summary()
        job.mark_finished(stats=stats.as_dict(), summary=summary)
        self.stdout.write(self.style.SUCCESS(summary))
