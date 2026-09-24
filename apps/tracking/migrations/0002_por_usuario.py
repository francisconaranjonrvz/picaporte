"""Favoritas, visitas y notas pasan a ser de cada usuario (ADR 0015)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.accounts.migration_utils import assign_owner



class Migration(migrations.Migration):
    dependencies = [
        ("tracking", "0001_initial"),
        ("companies", "0004_unmerge_chain_records"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="favorite",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorites",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="visit",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="visits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="note",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="favorite",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorites",
                to="companies.company",
            ),
        ),
        migrations.AlterField(
            model_name="visit",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="visits",
                to="companies.company",
            ),
        ),
        migrations.RunPython(assign_owner("tracking", "Favorite"), migrations.RunPython.noop),
        migrations.RunPython(assign_owner("tracking", "Visit"), migrations.RunPython.noop),
        migrations.RunPython(assign_owner("tracking", "Note"), migrations.RunPython.noop),
        migrations.AlterField(
            model_name="favorite",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="favorites",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="visit",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="visits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="note",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="notes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="favorite",
            constraint=models.UniqueConstraint(
                fields=("user", "company"), name="unique_user_favorite"
            ),
        ),
        migrations.AddConstraint(
            model_name="visit",
            constraint=models.UniqueConstraint(fields=("user", "company"), name="unique_user_visit"),
        ),
    ]
