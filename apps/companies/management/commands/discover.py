"""Descubre empresas en todas las fuentes (o las indicadas) y las fusiona en la BD.

Pensado para GitHub Actions (discover.yml): registra un JobRun con métricas
y resumen que la app muestra en la pestaña Explorar.

    uv run python manage.py discover                 # todas las fuentes
    uv run python manage.py discover --sources osm   # solo OSM
    uv run python manage.py discover --dry-run       # cuenta sin escribir
"""

from django.core.management.base import BaseCommand, CommandError

from apps.companies.services import discovery_summary, run_discovery
from apps.companies.sources import ADAPTERS
from apps.jobs.models import JobRun
from apps.jobs.services import begin_job


class Command(BaseCommand):
    help = "Descubre empresas en las fuentes configuradas y las fusiona (dedupe + confianza)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sources",
            default="",
            help=f"Fuentes separadas por comas (disponibles: {', '.join(ADAPTERS)}). Vacío = todas.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Solo cuenta; no escribe en la BD."
        )
        parser.add_argument("--job-id", type=int, default=None, help="JobRun creado por la app.")

    def handle(self, *args, sources, dry_run, job_id, **options):
        names = [s.strip() for s in sources.split(",") if s.strip()] or None
        if names and (unknown := [n for n in names if n not in ADAPTERS]):
            raise CommandError(f"Fuentes desconocidas: {', '.join(unknown)}")

        job = begin_job(JobRun.Kind.DISCOVER, job_id)
        try:
            results = run_discovery(names, dry_run=dry_run)
        except Exception as exc:
            job.mark_finished(stats={}, summary="", error=f"{type(exc).__name__}: {exc}")
            raise

        stats = {name: s.as_dict() for name, s in results.items()}
        text, errors = discovery_summary(results)
        summary = text + (" (dry-run)" if dry_run else "")
        all_failed = errors and len(errors) == len(results)
        job.mark_finished(
            stats=stats, summary=summary, error="\n".join(errors) if all_failed else ""
        )
        self.stdout.write(
            self.style.SUCCESS(summary) if not all_failed else self.style.ERROR(summary)
        )
        if all_failed:
            raise CommandError("Todas las fuentes han fallado.")
