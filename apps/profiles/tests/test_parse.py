import pytest

from django.urls import reverse

from apps.llm.client import LLMError, LLMResult
from apps.llm.models import LLMCall
from apps.profiles import services, views
from apps.profiles.models import Profile
from apps.profiles.schemas import EducationItem, ExperienceItem, LanguageItem, ParsedCV
from apps.profiles.services import apply_parsed, save_cv

PDF = b"%PDF-1.4\n%%EOF"

PARSED = ParsedCV(
    full_name="Laura Vidal",
    headline="Graduada en Publicidad y RRPP · eventos",
    summary="Me apasionan los eventos.",
    education=[
        EducationItem(title="Grado en Publicidad y RRPP", organization="UAB", period="2020-2024")
    ],
    experience=[
        ExperienceItem(
            title="Prácticas", organization="Agencia X", period="2024", summary="Apoyo en eventos."
        )
    ],
    skills=["Canva", "Meta Ads"],
    languages=[LanguageItem(language="Catalán", level="nativo")],
)


def _result(profile, cached=False):
    call, _ = LLMCall.objects.get_or_create(
        input_hash="x" * 64,
        defaults={
            "purpose": "cv_parse",
            "model": "claude-haiku-4-5",
            "prompt_version": "cv_parse_v1",
            "response": PARSED.model_dump(),
        },
    )
    return LLMResult(PARSED, call, cached)


@pytest.mark.django_db
def test_apply_parsed_rellena_campos_vacios_y_marca_trazabilidad(user):
    profile = Profile.objects.create(user=user)

    changed = apply_parsed(profile, _result(profile))

    profile.refresh_from_db()
    assert set(changed) == {
        "full_name",
        "headline",
        "summary",
        "education",
        "experience",
        "skills",
        "languages",
    }
    assert profile.full_name == "Laura Vidal"
    assert profile.skills == ["Canva", "Meta Ads"]
    assert profile.education[0]["organization"] == "UAB"
    assert profile.parsed_model == "claude-haiku-4-5"
    assert profile.parsed_prompt_version == "cv_parse_v1"
    assert profile.parsed_at is not None


@pytest.mark.django_db
def test_apply_parsed_respeta_lo_editado_a_mano_salvo_overwrite(user):
    profile = Profile.objects.create(user=user, full_name="Laura V.", skills=["Excel"])

    changed = apply_parsed(profile, _result(profile))
    assert "full_name" not in changed
    assert "skills" not in changed
    assert "summary" in changed
    assert profile.full_name == "Laura V."

    changed = apply_parsed(profile, _result(profile), overwrite=True)
    assert "full_name" in changed
    assert profile.full_name == "Laura Vidal"
    assert profile.skills == ["Canva", "Meta Ads"]


@pytest.mark.django_db
def test_analizar_por_htmx_devuelve_el_formulario_relleno(auth_client, user, monkeypatch):
    profile = Profile.objects.create(user=user)
    save_cv(profile, "cv.pdf", PDF)
    monkeypatch.setattr(views, "parse_cv", lambda cv: _result(profile))

    resp = auth_client.post(reverse("cv_parse"), HTTP_HX_REQUEST="true")

    assert resp.status_code == 200
    assert "Perfil actualizado" in resp.text
    assert 'value="Laura Vidal"' in resp.text
    assert "Canva, Meta Ads" in resp.text
    assert "Prácticas · Agencia X · 2024 — Apoyo en eventos." in resp.text
    assert "toast" in resp["HX-Trigger"]
    assert "<html" not in resp.text


@pytest.mark.django_db
def test_analizar_desde_cache_lo_indica(auth_client, user, monkeypatch):
    profile = Profile.objects.create(user=user)
    save_cv(profile, "cv.pdf", PDF)
    monkeypatch.setattr(views, "parse_cv", lambda cv: _result(profile, cached=True))

    resp = auth_client.post(reverse("cv_parse"))

    assert "sin coste" in resp.text


@pytest.mark.django_db
def test_analizar_sin_cv_o_con_error_de_ia(auth_client, user, monkeypatch):
    resp = auth_client.post(reverse("cv_parse"))
    assert "Sube primero tu CV" in resp.text

    profile = Profile.objects.get(user=user)
    save_cv(profile, "cv.pdf", PDF)

    def falla(cv):
        raise LLMError("La IA ha tardado demasiado; vuelve a intentarlo.")

    monkeypatch.setattr(views, "parse_cv", falla)
    resp = auth_client.post(reverse("cv_parse"))
    assert resp.status_code == 200
    assert 'role="alert"' in resp.text
    assert "tardado demasiado" in resp.text


@pytest.mark.django_db
def test_parse_cv_usa_el_prompt_versionado_y_el_pdf(user, monkeypatch):
    profile = Profile.objects.create(user=user)
    cv = save_cv(profile, "cv.pdf", PDF)
    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return _result(profile)

    monkeypatch.setattr(services, "call_structured", fake_call)

    services.parse_cv(cv)

    assert captured["prompt_version"] == "cv_parse_v1"
    assert captured["pdf_bytes"] == PDF
    assert captured["output_model"] is ParsedCV
    assert "nunca inventes" in captured["system"]
