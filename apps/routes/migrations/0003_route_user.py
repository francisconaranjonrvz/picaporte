"""Cada ruta es de un usuario (ADR 0015)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.accounts.migration_utils import assign_owner


class Migration(migrations.Migration):
    dependencies = [
        ("routes", "0002_routestop_closed_note"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="routes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_owner("routes", "Route"), migrations.RunPython.noop),
        migrations.AlterField(
            model_name="route",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="routes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
