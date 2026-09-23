"""Importa ofertas desde un archivo (scan-history.tsv de career-ops, CSV o JSON).

uv run python manage.py import_offers ../career-ops/data/scan-history.tsv
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.offers.importers import ImportFormatError, parse
from apps.offers.services import import_offers


class Command(BaseCommand):
    help = "Importa ofertas de empleo y las cruza con las empresas."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)

    def handle(self, *args, path: Path, **options):
        if not path.is_file():
            raise CommandError(f"No existe {path}")
        try:
            result = parse(path.name, path.read_bytes())
        except ImportFormatError as exc:
            raise CommandError(str(exc)) from exc
        stats = import_offers(result)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.source}: {stats.total} ofertas ({stats.created} nuevas, "
                f"{stats.updated} actualizadas), {stats.matched} cruzadas con "
                f"{len(stats.matched_companies)} empresas, {stats.skipped} descartadas."
            )
        )
