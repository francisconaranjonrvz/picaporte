import pytest

from django.urls import reverse

from apps.catalog.models import Category, Zone
from apps.profiles.forms import ListField, ProfileForm, RecordsField
from apps.profiles.models import Profile


def test_listfield_ida_y_vuelta():
    field = ListField()

    assert field.to_python("Canva, Meta Ads\n redacción ,, ") == ["Canva", "Meta Ads", "redacción"]
    assert field.to_python("") == []
    assert field.prepare_value(["Canva", "Meta Ads"]) == "Canva, Meta Ads"


def test_recordsfield_ida_y_vuelta():
    field = RecordsField(keys=("title", "organization", "period"), summary_key="summary")
    records = [
        {
            "title": "Prácticas",
            "organization": "Agencia X",
            "period": "2024",
            "summary": "Eventos.",
        },
        {"title": "Camarera", "organization": "", "period": "", "summary": ""},
    ]

    text = field.prepare_value(records)
    assert text == "Prácticas · Agencia X · 2024 — Eventos.\nCamarera"
    assert field.to_python(text) == records
    assert field.to_python("Solo título") == [
        {"title": "Solo título", "organization": "", "period": "", "summary": ""}
    ]


@pytest.mark.django_db
def test_guardar_perfil_con_chips_y_listas(auth_client, user):
    data = {
        "full_name": "Laura Vidal",
        "headline": "Publicidad y eventos",
        "summary": "Hola",
        "skills": "Canva, Meta Ads",
        "languages": "Catalán · nativo\nInglés · B2",
        "education": "Grado en Publicidad · UAB · 2020-2024",
        "experience": "Prácticas · Agencia X · 2024 — Eventos",
        "categories": [
            Category.objects.get(slug="eventos").pk,
            Category.objects.get(slug="publicidad").pk,
        ],
        "zones": [Zone.objects.get(slug="poblenou").pk],
        "company_sizes": ["micro", "pequena"],
        "work_languages": ["es", "ca"],
        "interests": "Marcas de moda sostenible.",
    }

    resp = auth_client.post(reverse("profile_update"), data)

    assert resp.status_code == 302
    profile = Profile.objects.get(user=user)
    assert profile.skills == ["Canva", "Meta Ads"]
    assert profile.languages == [
        {"language": "Catalán", "level": "nativo"},
        {"language": "Inglés", "level": "B2"},
    ]
    assert profile.experience[0]["summary"] == "Eventos"
    assert set(profile.categories.values_list("slug", flat=True)) == {"eventos", "publicidad"}
    assert list(profile.zones.values_list("slug", flat=True)) == ["poblenou"]
    assert profile.company_sizes == ["micro", "pequena"]
    assert profile.work_languages == ["es", "ca"]
    assert profile.is_complete is True
    page = auth_client.get(reverse("perfil"))
    assert "Perfil guardado" in page.text
    assert page.text.count(" checked>") == 7  # 2 categorías + 1 zona + 2 tamaños + 2 idiomas


@pytest.mark.django_db
def test_valores_invalidos_devuelven_400_con_errores(auth_client):
    resp = auth_client.post(
        reverse("profile_update"), {"company_sizes": ["gigante"], "full_name": "x" * 121}
    )

    assert resp.status_code == 400
    assert "field-error" in resp.text


@pytest.mark.django_db
def test_form_inicial_muestra_las_listas_como_texto(user):
    profile = Profile.objects.create(
        user=user, skills=["Canva"], languages=[{"language": "Inglés", "level": "B2"}]
    )

    form = ProfileForm(instance=profile)
    html = form.as_div()

    assert "Canva</textarea>" in html
    assert "Inglés · B2" in html
    assert 'class="chip' in html
