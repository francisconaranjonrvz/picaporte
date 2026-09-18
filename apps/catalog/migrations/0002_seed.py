"""Siembra las categorías y zonas iniciales (idempotente)."""

from django.db import migrations

from apps.catalog.seeds import CATEGORIES, ZONES


def seed(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Zone = apps.get_model("catalog", "Zone")
    for order, (slug, name) in enumerate(CATEGORIES):
        Category.objects.update_or_create(slug=slug, defaults={"name": name, "order": order})
    for order, (slug, name, bbox) in enumerate(ZONES):
        Zone.objects.update_or_create(slug=slug, defaults={"name": name, "order": order, "bbox": bbox})


def unseed(apps, schema_editor):
    apps.get_model("catalog", "Category").objects.filter(slug__in=[s for s, _ in CATEGORIES]).delete()
    apps.get_model("catalog", "Zone").objects.filter(slug__in=[s for s, _, _ in ZONES]).delete()


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
