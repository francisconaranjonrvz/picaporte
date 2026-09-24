"""Añade los sectores opcionales y retira coworkings y marcas (sin borrarlos)."""

from django.db import migrations

from apps.catalog.seeds import CATEGORIES, OPTIONAL_CATEGORIES, RETIRED_CATEGORIES


def forwards(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(slug__in=RETIRED_CATEGORIES).update(is_active=False)
    for order, (slug, name) in enumerate(OPTIONAL_CATEGORIES, start=len(CATEGORIES)):
        Category.objects.update_or_create(
            slug=slug, defaults={"name": name, "order": order, "is_active": True}
        )


def backwards(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(slug__in=[s for s, _ in OPTIONAL_CATEGORIES]).delete()
    Category.objects.filter(slug__in=RETIRED_CATEGORIES).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_seed")]

    operations = [migrations.RunPython(forwards, backwards)]
