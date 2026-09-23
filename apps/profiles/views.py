import io
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.enrichment.profile import stale_scores_count
from apps.jobs.models import JobRun
from apps.jobs.views import latest
from apps.llm.client import LLMError

from .forms import CVUploadForm, ProfileForm
from .models import MAX_CV_BYTES, CVDocument, Profile
from .services import apply_parsed, parse_cv, save_cv

FIELD_LABELS = {
    "full_name": "nombre",
    "headline": "titular",
    "summary": "resumen",
    "education": "formación",
    "experience": "experiencia",
    "skills": "habilidades",
    "languages": "idiomas",
}


def _profile(request) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return profile


def _cv(profile: Profile) -> CVDocument | None:
    return (
        CVDocument.objects.filter(profile=profile)
        .only("id", "filename", "size", "uploaded_at")
        .first()
    )


def _form_context(profile: Profile, form: ProfileForm | None = None, **extra):
    return {
        "profile": profile,
        "form": form or ProfileForm(instance=profile),
        # Puntuaciones hechas con un perfil anterior: se ofrece recalcularlas (fase 4).
        "stale_scores": stale_scores_count(profile),
        "enrich_job": latest(JobRun.Kind.ENRICH),
        **extra,
    }


def perfil(request):
    profile = _profile(request)
    context = {
        "title": "Perfil",
        "cv": _cv(profile),
        "upload_form": CVUploadForm(),
        "max_cv_mb": MAX_CV_BYTES // (1024 * 1024),
        **_form_context(profile),
    }
    return render(request, "profiles/perfil.html", context)


@require_POST
def cv_upload(request):
    profile = _profile(request)
    form = CVUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Selecciona un archivo PDF.")
        return redirect("perfil")
    upload = form.cleaned_data["cv"]
    if upload.size > MAX_CV_BYTES:
        messages.error(request, f"El CV supera el límite de {MAX_CV_BYTES // (1024 * 1024)} MB.")
        return redirect("perfil")
    try:
        save_cv(profile, upload.name, upload.read())
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("perfil")
    messages.success(request, "CV guardado. Ya puedes analizarlo con la IA.")
    return redirect("perfil")


@require_GET
def cv_download(request):
    profile = _profile(request)
    cv = get_object_or_404(CVDocument, profile=profile)
    # FileResponse codifica el nombre (RFC 5987) y escapa comillas; a mano se rompería con "Currículum".
    return FileResponse(
        io.BytesIO(bytes(cv.data)),
        content_type="application/pdf",
        as_attachment=False,
        filename=cv.filename,
    )


@require_POST
def cv_delete(request):
    profile = _profile(request)
    deleted, _ = CVDocument.objects.filter(profile=profile).delete()
    if not deleted:
        raise Http404
    messages.success(request, "CV eliminado.")
    return redirect("perfil")


@require_POST
def cv_parse(request):
    """Analiza el CV con la IA y devuelve el formulario actualizado (parcial HTMX)."""
    profile = _profile(request)
    cv = CVDocument.objects.filter(profile=profile).first()
    if cv is None:
        return _parse_result(request, profile, error="Sube primero tu CV en PDF.")
    overwrite = request.POST.get("overwrite") == "1"
    try:
        result = parse_cv(cv)
    except LLMError as exc:
        return _parse_result(request, profile, error=str(exc))
    changed = apply_parsed(profile, result, overwrite=overwrite)
    if changed:
        labels = ", ".join(FIELD_LABELS[f] for f in changed)
        note = f"Perfil actualizado ({labels})."
    else:
        note = "El perfil ya tenía todos los datos; nada que cambiar."
    if result.cached:
        note += " Respuesta reutilizada de la caché (sin coste)."
    return _parse_result(request, profile, note=note)


def _parse_result(request, profile: Profile, *, note: str = "", error: str = ""):
    response = render(
        request,
        "profiles/_profile_form.html",
        _form_context(profile, parse_note=note, parse_error=error),
    )
    response["HX-Trigger"] = json.dumps({"toast": error or note})
    return response


@require_POST
def profile_update(request):
    profile = _profile(request)
    form = ProfileForm(request.POST, instance=profile)
    if form.is_valid():
        form.save()
        messages.success(request, "Perfil guardado.")
        return redirect("perfil")
    context = {
        "title": "Perfil",
        "cv": _cv(profile),
        "upload_form": CVUploadForm(),
        "max_cv_mb": MAX_CV_BYTES // (1024 * 1024),
        **_form_context(profile, form),
    }
    return render(request, "profiles/perfil.html", context, status=400)
