import json
from datetime import date, timedelta

import pytest

from django.urls import reverse

from apps.companies.models import Company
from apps.tracking import services
from apps.tracking.models import Favorite, Note, Visit


@pytest.fixture
def sol(db):
    return Company.objects.create(name="Agencia Sol")


def _trigger(resp):
    return json.loads(resp["HX-Trigger"])


def test_favorita_se_anade_al_final_y_se_quita(db):
    a, b = Company.objects.create(name="A"), Company.objects.create(name="B")
    fa = services.toggle_favorite(a)
    fb = services.toggle_favorite(b)
    assert (fa.position, fb.position) == (1, 2)
    assert services.toggle_favorite(a) is None
    assert list(Favorite.objects.values_list("company__name", flat=True)) == ["B"]


def test_reordenar_ignora_ids_ajenos_y_repetidos(db):
    companies = [Company.objects.create(name=n) for n in "ABC"]
    for c in companies:
        services.toggle_favorite(c)
    a, b, c = companies
    ajena = Company.objects.create(name="No favorita")

    assert services.reorder_favorites([c.pk, ajena.pk, a.pk, c.pk, b.pk]) == 3
    assert list(Favorite.objects.values_list("company__name", flat=True)) == ["C", "A", "B"]


def test_cambiar_estado_deja_nota_solo_si_cambia(sol):
    services.set_status(sol, Visit.Status.PLANNED)
    services.set_status(sol, Visit.Status.PLANNED)
    services.set_status(sol, Visit.Status.CV_DELIVERED)
    assert Visit.objects.get(company=sol).status == "cv_entregado"
    assert list(Note.objects.values_list("text", flat=True)) == [
        "Estado: CV entregado",
        "Estado: Planificado",
    ]


def test_notas_vacias_no_se_guardan(sol):
    assert services.add_note(sol, "   ") is None
    assert services.add_note(sol, " Hablé con Marta ").text == "Hablé con Marta"


def test_corazon_por_htmx(auth_client, sol):
    resp = auth_client.post(reverse("favorite_toggle", args=[sol.pk]))
    assert resp.status_code == 200
    assert 'aria-pressed="true"' in resp.text
    assert _trigger(resp) == {"toast": "Añadida a favoritas"}

    resp = auth_client.post(reverse("favorite_toggle", args=[sol.pk]))
    assert 'aria-pressed="false"' in resp.text
    assert not Favorite.objects.exists()
    assert auth_client.get(reverse("favorite_toggle", args=[sol.pk])).status_code == 405


def test_estado_por_htmx(auth_client, sol):
    resp = auth_client.post(reverse("status_update", args=[sol.pk]), {"status": "volver"})
    assert resp.status_code == 200
    assert "badge-warning" in resp.text
    assert _trigger(resp) == {"toast": "Estado: Volver", "notes-changed": True}
    assert (
        auth_client.post(reverse("status_update", args=[sol.pk]), {"status": "x"}).status_code
        == 400
    )


def test_seguimiento_y_notas_por_htmx(auth_client, sol):
    resp = auth_client.post(
        reverse("visit_update", args=[sol.pk]),
        {"contact": "Marta (cuentas)", "next_action": "Llamar", "next_action_on": "2026-10-01"},
    )
    assert resp.status_code == 200
    visit = Visit.objects.get(company=sol)
    assert (visit.contact, visit.next_action_on) == ("Marta (cuentas)", date(2026, 10, 1))

    resp = auth_client.post(reverse("visit_update", args=[sol.pk]), {"next_action_on": "mañana"})
    # 200 para que htmx 2 haga el swap y se vean los errores (no pinta los 4xx).
    assert resp.status_code == 200
    assert "field-error" in resp.text
    assert "HX-Trigger" not in resp  # sin el aviso de "guardado"

    resp = auth_client.post(reverse("note_add", args=[sol.pk]), {"text": "Dejé el CV en recepción"})
    assert "Dejé el CV en recepción" in resp.text
    assert "Dejé el CV en recepción" in auth_client.get(reverse("notes", args=[sol.pk])).text


def test_pagina_de_favoritas(auth_client, db):
    assert "Aún no hay favoritas" in auth_client.get(reverse("favoritas")).text

    a, b = Company.objects.create(name="Agencia A"), Company.objects.create(name="Agencia B")
    services.toggle_favorite(a)
    services.toggle_favorite(b)
    Visit.objects.create(
        company=b,
        next_action="Volver con portfolio",
        next_action_on=date.today() - timedelta(days=1),
    )

    html = auth_client.get(reverse("favoritas")).text
    assert html.index(">Agencia A</h2>") < html.index(">Agencia B</h2>")
    assert "Próximas acciones" in html
    assert "Volver con portfolio" in html
    assert "badge-danger" in html  # acción atrasada
    assert "sortable.min" in html


def test_reordenar_y_editar_favorita_por_post(auth_client, db):
    a, b = Company.objects.create(name="A"), Company.objects.create(name="B")
    services.toggle_favorite(a)
    services.toggle_favorite(b)

    resp = auth_client.post(reverse("favorites_reorder"), {"ids": f"{b.pk},{a.pk}"})
    assert resp.status_code == 204
    assert list(Favorite.objects.values_list("company__name", flat=True)) == ["B", "A"]
    assert auth_client.post(reverse("favorites_reorder"), {"ids": "1,x"}).status_code == 400

    resp = auth_client.post(reverse("favorite_update", args=[a.pk]), {"priority": "alta"})
    assert resp.status_code == 204
    resp = auth_client.post(
        reverse("favorite_update", args=[a.pk]), {"note": "  Preguntar por Marta "}
    )
    fav = Favorite.objects.get(company=a)
    assert (fav.priority, fav.note) == ("alta", "Preguntar por Marta")
    assert (
        auth_client.post(
            reverse("favorite_update", args=[a.pk]), {"priority": "urgente"}
        ).status_code
        == 400
    )
    assert (
        auth_client.post(reverse("favorite_update", args=[999]), {"priority": "alta"}).status_code
        == 404
    )


def test_todo_requiere_login(client, sol):
    for name in ("favorite_toggle", "status_update", "visit_update", "note_add"):
        assert client.post(reverse(name, args=[sol.pk])).status_code == 302
    assert client.get(reverse("favoritas")).status_code == 302
