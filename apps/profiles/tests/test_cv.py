import pytest

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.profiles.models import MAX_CV_BYTES, CVDocument, Profile
from apps.profiles.services import save_cv, validate_pdf

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def profile(user):
    return Profile.objects.create(user=user)


def test_validate_pdf_rechaza_lo_que_no_es_pdf():
    with pytest.raises(ValidationError, match="vacío"):
        validate_pdf("cv.pdf", b"")
    with pytest.raises(ValidationError, match="PDF válido"):
        validate_pdf("cv.pdf", b"hola")
    with pytest.raises(ValidationError, match="extensión"):
        validate_pdf("cv.docx", PDF)
    with pytest.raises(ValidationError, match="límite"):
        validate_pdf("cv.pdf", b"%PDF-" + b"0" * MAX_CV_BYTES)
    validate_pdf("CV Laura.PDF", PDF)


@pytest.mark.django_db
def test_save_cv_sustituye_el_anterior(profile):
    save_cv(profile, "v1.pdf", PDF)
    cv = save_cv(profile, "v2.pdf", PDF + b"\n%v2")

    assert CVDocument.objects.count() == 1
    assert cv.filename == "v2.pdf"
    assert cv.size == len(PDF) + 4
    assert len(cv.sha256) == 64
    assert bytes(CVDocument.objects.get().data) == PDF + b"\n%v2"


def test_subir_cv_por_formulario(auth_client, user):
    resp = auth_client.post(
        reverse("cv_upload"), {"cv": SimpleUploadedFile("mi cv.pdf", PDF, "application/pdf")}
    )

    assert resp.status_code == 302
    assert resp.url == reverse("perfil")
    cv = CVDocument.objects.get(profile__user=user)
    assert cv.filename == "mi cv.pdf"
    page = auth_client.get(reverse("perfil"))
    assert "mi cv.pdf" in page.text
    assert "CV guardado" in page.text
    assert "Analizar con IA" in page.text


def test_subir_archivo_no_pdf_muestra_error(auth_client):
    resp = auth_client.post(
        reverse("cv_upload"), {"cv": SimpleUploadedFile("cv.pdf", b"no soy pdf")}
    )

    assert resp.status_code == 302
    assert CVDocument.objects.count() == 0
    assert "no es un PDF válido" in auth_client.get(reverse("perfil")).text


def test_subir_sin_archivo(auth_client):
    auth_client.post(reverse("cv_upload"), {})

    assert "Selecciona un archivo PDF" in auth_client.get(reverse("perfil")).text


def test_descargar_y_borrar_cv(auth_client, user):
    profile = Profile.objects.create(user=user)
    save_cv(profile, "cv.pdf", PDF)

    resp = auth_client.get(reverse("cv_download"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert 'filename="cv.pdf"' in resp["Content-Disposition"]
    assert b"".join(resp.streaming_content) == PDF

    resp = auth_client.post(reverse("cv_delete"))
    assert resp.status_code == 302
    assert CVDocument.objects.count() == 0
    assert auth_client.get(reverse("cv_download")).status_code == 404
    assert auth_client.post(reverse("cv_delete")).status_code == 404


def test_el_cv_es_privado(client, db):
    assert client.get(reverse("cv_download")).status_code == 302
    assert client.get(reverse("perfil")).status_code == 302


def test_subir_cv_demasiado_grande(auth_client):
    grande = SimpleUploadedFile("cv.pdf", b"%PDF-" + b"0" * MAX_CV_BYTES, "application/pdf")

    auth_client.post(reverse("cv_upload"), {"cv": grande})

    assert CVDocument.objects.count() == 0
    assert "supera el límite" in auth_client.get(reverse("perfil")).text


@pytest.mark.django_db
def test_descarga_codifica_nombres_no_ascii(auth_client, user):
    profile = Profile.objects.create(user=user)
    save_cv(profile, 'Currículum "Laura".pdf', PDF)

    resp = auth_client.get(reverse("cv_download"))

    disposition = resp["Content-Disposition"]
    assert disposition.startswith("inline;")
    assert "filename*=utf-8''Curr%C3%ADculum" in disposition


@pytest.mark.django_db
def test_sustituir_el_cv_actualiza_la_fecha(user):
    profile = Profile.objects.create(user=user)
    primero = save_cv(profile, "v1.pdf", PDF)
    CVDocument.objects.filter(pk=primero.pk).update(
        uploaded_at=primero.uploaded_at.replace(year=2020)
    )

    segundo = save_cv(profile, "v2.pdf", PDF + b"\n%v2")

    assert segundo.pk == primero.pk
    assert segundo.uploaded_at.year >= 2026
