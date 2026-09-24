"""Las ofertas importadas son de cada usuario; la URL es única por usuario (ADR 0015)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.accounts.migration_utils import assign_owner


class Migration(migrations.Migration):
    dependencies = [
        ("offers", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="joboffer",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="job_offers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_owner("offers", "JobOffer"), migrations.RunPython.noop),
        migrations.AlterField(
            model_name="joboffer",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="job_offers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="joboffer", name="url", field=models.URLField(max_length=600)
        ),
        migrations.AddConstraint(
            model_name="joboffer",
            constraint=models.UniqueConstraint(fields=("user", "url"), name="unique_user_offer"),
        ),
    ]
