"""La puntuación pasa de `Enrichment` (global) a `FitScore` (por usuario) (ADR 0015).

Las notas que ya existían se copian a la cuenta principal (ver `assign_owner`).
"""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

SCORE_FIELDS = [
    "fit_score",
    "fit_breakdown",
    "fit_reason",
    "hook",
    "scored_at",
    "profile_hash",
    "scoring_model",
    "scoring_prompt_version",
]


def copy_scores(apps, schema_editor):
    Enrichment = apps.get_model("enrichment", "Enrichment")
    FitScore = apps.get_model("enrichment", "FitScore")
    scored = Enrichment.objects.filter(fit_score__isnull=False)
    if not scored.exists():
        return
    User = apps.get_model("auth", "User")
    owner = (
        User.objects.filter(is_superuser=True).order_by("pk").first()
        or User.objects.order_by("pk").first()
    )
    if owner is None:
        return
    FitScore.objects.bulk_create(
        [
            FitScore(
                user=owner,
                company_id=e.company_id,
                fit_score=e.fit_score,
                fit_breakdown=e.fit_breakdown,
                fit_reason=e.fit_reason,
                hook=e.hook,
                scored_at=e.scored_at or django.utils.timezone.now(),
                profile_hash=e.profile_hash,
                scoring_model=e.scoring_model,
                scoring_prompt_version=e.scoring_prompt_version,
            )
            for e in scored
        ],
        batch_size=500,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("enrichment", "0003_fit_breakdown"),
        ("companies", "0004_unmerge_chain_records"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FitScore",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fit_score", models.PositiveSmallIntegerField(verbose_name="encaje (0-100)")),
                ("fit_breakdown", models.JSONField(blank=True, default=dict, verbose_name="encaje por criterios")),
                ("fit_reason", models.TextField(blank=True, verbose_name="justificación")),
                ("hook", models.TextField(blank=True, verbose_name="gancho para presentarse")),
                ("scored_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("profile_hash", models.CharField(blank=True, max_length=64)),
                ("scoring_model", models.CharField(blank=True, max_length=64)),
                ("scoring_prompt_version", models.CharField(blank=True, max_length=64)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fit_scores",
                        to="companies.company",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fit_scores",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "encaje",
                "verbose_name_plural": "encajes",
                "indexes": [models.Index(fields=["user", "-fit_score"], name="enrichment__user_id_1bb48a_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "company"), name="unique_user_fit_score")
                ],
            },
        ),
        migrations.RunPython(copy_scores, migrations.RunPython.noop),
        migrations.RemoveIndex(model_name="enrichment", name="enrichment__fit_sco_804875_idx"),
        *[migrations.RemoveField(model_name="enrichment", name=name) for name in SCORE_FIELDS],
    ]
