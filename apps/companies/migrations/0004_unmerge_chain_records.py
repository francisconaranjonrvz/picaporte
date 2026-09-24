"""Suelta los registros de una misma fuente fusionados en una sola empresa.

Hasta la revisión de septiembre de 2026 el dedupe por dominio ignoraba la distancia y
fundía todas las sedes de una cadena (8 Cloudworks -> 1). Aquí se conserva el registro
más antiguo de cada fuente y se borran los demás: el siguiente descubrimiento los vuelve
a crear, ya como empresas separadas si están a más de 500 m (o fusionados otra vez si
son el mismo sitio).
"""

from django.db import migrations
from django.db.models import Count, Min


def unmerge(apps, schema_editor):
    SourceRecord = apps.get_model("companies", "SourceRecord")
    groups = (
        SourceRecord.objects.values("company_id", "source")
        .annotate(n=Count("id"), keep=Min("id"))
        .filter(n__gt=1)
    )
    for group in groups:
        SourceRecord.objects.filter(
            company_id=group["company_id"], source=group["source"]
        ).exclude(pk=group["keep"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0003_normalize_websites"),
    ]

    operations = [
        migrations.RunPython(unmerge, migrations.RunPython.noop),
    ]
