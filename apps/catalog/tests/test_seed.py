import pytest

from apps.catalog.models import Category, Zone
from apps.catalog.seeds import CATEGORIES, ZONES


@pytest.mark.django_db
def test_la_migracion_siembra_el_catalogo():
    assert list(Category.objects.values_list("slug", flat=True)) == [s for s, _ in CATEGORIES]
    assert list(Zone.objects.values_list("slug", flat=True)) == [s for s, _, _ in ZONES]


@pytest.mark.django_db
def test_las_zonas_tienen_bbox_dentro_de_barcelona():
    for zone in Zone.objects.all():
        lng_min, lat_min, lng_max, lat_max = zone.bbox
        assert 2.05 < lng_min < lng_max < 2.30
        assert 41.30 < lat_min < lat_max < 41.50
