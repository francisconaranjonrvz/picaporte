"""Crea (o mantiene) el único usuario de Picaporte de forma idempotente.

`createsuperuser --noinput` falla si el usuario ya existe; este comando se
puede ejecutar en cada despliegue desde GitHub Actions sin efectos
secundarios. La contraseña solo se fija al crear o con --reset-password.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Crea o actualiza el usuario único a partir de PICAPORTE_USER/PASSWORD/EMAIL."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=os.environ.get("PICAPORTE_USER"))
        parser.add_argument("--password", default=os.environ.get("PICAPORTE_PASSWORD"))
        parser.add_argument("--email", default=os.environ.get("PICAPORTE_EMAIL", ""))
        parser.add_argument(
            "--reset-password",
            action="store_true",
            help="Vuelve a fijar la contraseña aunque el usuario ya exista.",
        )

    def handle(self, *args, username, password, email, reset_password, **options):
        if not username or not password:
            raise CommandError(
                "Faltan PICAPORTE_USER / PICAPORTE_PASSWORD (o --username/--password)."
            )

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        if created or reset_password:
            user.set_password(password)
            user.save(update_fields=["password"])

        estado = (
            "creado" if created else ("contraseña actualizada" if reset_password else "ya existía")
        )
        self.stdout.write(self.style.SUCCESS(f"Usuario {username}: {estado}."))
