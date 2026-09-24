"""Importa ofertas desde un archivo (scan-history.tsv de career-ops, CSV o JSON).

uv run python manage.py import_offers ../career-ops/data/scan-history.tsv --user laura
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.offers.importers import ImportFormatError, parse
from apps.offers.services import import_offers


class Command(BaseCommand):
    help = "Importa ofertas de empleo y las cruza con las empresas."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument("--user", required=True, help="Usuario dueño de las ofertas.")

    def handle(self, *args, path: Path, user: str, **options):
        owner = get_user_model().objects.filter(username=user).first()
        if owner is None:
            raise CommandError(f"No existe el usuario {user}")
        if not path.is_file():
            raise CommandError(f"No existe {path}")
        try:
            result = parse(path.name, path.read_bytes())
        except ImportFormatError as exc:
            raise CommandError(str(exc)) from exc
        stats = import_offers(owner, result)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.source}: {stats.total} ofertas ({stats.created} nuevas, "
                f"{stats.updated} actualizadas), {stats.matched} cruzadas con "
                f"{len(stats.matched_companies)} empresas, {stats.skipped} descartadas."
            )
        )
