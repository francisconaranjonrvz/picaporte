"""Búsqueda personalizada: busca empresas de los sectores del perfil y las puntúa.

Es lo que se lanza al guardar el perfil (personalize.yml), a la manera de un
escaneo de career-ops: descubrir (sectores básicos + los opcionales del perfil),
rastrear y analizar las webs nuevas y puntuar el encaje de todas con el perfil
actual. Registra un único JobRun con el resumen de las dos fases.

    uv run python manage.py personalize                   # descubrir + enriquecer + puntuar
    uv run python manage.py personalize --skip-discover   # sectores sin cambios: solo puntuar
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.companies.services import discovery_summary, run_discovery
from apps.enrichment.services import run_enrichment
from apps.jobs.models import JobRun
from apps.jobs.services import begin_job
from apps.llm.models import LLMCall


class Command(BaseCommand):
    help = "Busca empresas de los sectores del perfil, analiza sus webs y puntúa el encaje."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-discover", action="store_true", help="No buscar empresas nuevas."
        )
        parser.add_argument("--limit", type=int, default=150, help="Máximo de webs a rastrear.")
        parser.add_argument(
            "--max-minutes",
            type=float,
            default=40,
            help="Presupuesto del enriquecimiento (el descubrimiento va aparte).",
        )
        parser.add_argument("--job-id", type=int, default=None, help="JobRun creado por la app.")

    def handle(self, *args, skip_discover, limit, max_minutes, job_id, **options):
        job = begin_job(JobRun.Kind.PERSONALIZE, job_id)
        lines, stats, errors = [], {}, []
        try:
            if not skip_discover:
                results = run_discovery()
                text, errors = discovery_summary(results)
                lines.append(text)
                stats["discover"] = {name: s.as_dict() for name, s in results.items()}
            enrich = run_enrichment(mode="all", limit=limit, max_minutes=max_minutes)
        except Exception as exc:
            job.mark_finished(stats=stats, summary="\n".join(lines), error=f"{exc!r}")
            raise
        lines.append(enrich.summary())
        stats["enrich"] = enrich.as_dict()

        job.cost_usd = LLMCall.objects.filter(created_at__gte=job.started_at).aggregate(
            total=Sum("cost_usd")
        )["total"] or Decimal(0)
        job.save(update_fields=["cost_usd"])
        summary = "\n".join(lines)
        # Una fuente caída no invalida la búsqueda: se avisa, pero el ranking sí se actualizó.
        job.mark_finished(stats=stats, summary=summary + "".join(f"\n{e}" for e in errors))
        self.stdout.write(self.style.SUCCESS(summary))
