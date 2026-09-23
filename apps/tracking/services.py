"""Casos de uso del seguimiento. Todo es local: nada se envía nunca a las empresas."""

from django.db import transaction
from django.db.models import Max

from apps.companies.models import Company

from .models import Favorite, Note, Visit


def toggle_favorite(company: Company) -> Favorite | None:
    """Añade la empresa al final de favoritas, o la quita. Devuelve la favorita o None."""
    deleted, _ = Favorite.objects.filter(company=company).delete()
    if deleted:
        return None
    last = Favorite.objects.aggregate(last=Max("position"))["last"]
    return Favorite.objects.create(company=company, position=(last or 0) + 1)


@transaction.atomic
def reorder_favorites(company_ids: list[int]) -> int:
    """Guarda el orden manual (los ids que no son favoritas se ignoran)."""
    favorites = {f.company_id: f for f in Favorite.objects.filter(company_id__in=company_ids)}
    ordered = []
    for position, company_id in enumerate(company_ids, start=1):
        favorite = favorites.get(company_id)
        if favorite is not None and favorite not in ordered:
            favorite.position = position
            ordered.append(favorite)
    Favorite.objects.bulk_update(ordered, ["position"])
    return len(ordered)


def visit_for(company: Company) -> Visit:
    visit, _ = Visit.objects.get_or_create(company=company)
    return visit


@transaction.atomic
def set_status(company: Company, status: str) -> Visit:
    """Cambia el estado y deja constancia en el historial de notas."""
    visit = visit_for(company)
    if visit.status != status:
        visit.status = status
        visit.save(update_fields=["status", "updated_at"])
        Note.objects.create(
            company=company,
            text=f"Estado: {visit.get_status_display()}",
            is_status_change=True,
        )
    return visit


def add_note(company: Company, text: str) -> Note | None:
    text = text.strip()
    if not text:
        return None
    return Note.objects.create(company=company, text=text[:2000])
