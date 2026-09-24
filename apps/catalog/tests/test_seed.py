import pytest

from apps.catalog.models import Category, Zone
from apps.catalog.seeds import CATEGORIES, OPTIONAL_CATEGORIES, RETIRED_CATEGORIES, ZONES
from apps.companies.relevance import ALL_SECTORS


@pytest.mark.django_db
def test_la_migracion_siembra_el_catalogo():
    seeded = [s for s, _ in CATEGORIES + OPTIONAL_CATEGORIES]
    assert list(Category.objects.values_list("slug", flat=True)) == seeded
    active = set(Category.objects.filter(is_active=True).values_list("slug", flat=True))
    assert active == set(seeded) - set(RETIRED_CATEGORIES)
    assert active == ALL_SECTORS  # cada sector activo tiene reglas de búsqueda, y viceversa
    assert list(Zone.objects.values_list("slug", flat=True)) == [s for s, _, _ in ZONES]


@pytest.mark.django_db
def test_las_zonas_tienen_bbox_dentro_de_barcelona():
    for zone in Zone.objects.all():
        lng_min, lat_min, lng_max, lat_max = zone.bbox
        assert 2.05 < lng_min < lng_max < 2.30
        assert 41.30 < lat_min < lat_max < 41.50
