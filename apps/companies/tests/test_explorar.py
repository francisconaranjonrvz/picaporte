from datetime import datetime

import pytest

from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Category, Zone
from apps.companies import filters as filters_module
from apps.companies.filters import CompanyFilter
from apps.companies.models import Company
from apps.enrichment.models import Enrichment
from apps.tracking.models import Favorite, Visit


def _company(name, score=None, category=None, zone=None, catalan="desconocido", **kw):
    company = Company.objects.create(
        name=name,
        category=Category.objects.get(slug=category) if category else None,
        zone=Zone.objects.get(slug=zone) if zone else None,
        **kw,
    )
    if score is not None or catalan != "desconocido":
        Enrichment.objects.create(
            company=company,
            crawl_status="ok",
            fit_score=score,
            requires_catalan=catalan,
            services=["eventos corporativos"],
        )
    return company


def _names(data):
    form = CompanyFilter(data)
    return [c.name for c in form.apply()]


@pytest.fixture
def companies(db):
    return {
        "a": _company("Agencia Sol", 85, "publicidad", "eixample", confidence_score=80),
        "b": _company("Eventos Mar", 55, "eventos", "gracia", catalan="si", confidence_score=40),
        "c": _company("Cowork Luna", None, "coworkings", "poblenou", confidence_score=60),
        "d": _company("Retirada", 99, "publicidad", is_active=False),
    }


def test_orden_por_encaje_con_nulos_al_final(companies):
    assert _names({}) == ["Agencia Sol", "Eventos Mar", "Cowork Luna"]
    assert _names({"sort": "confianza"}) == ["Agencia Sol", "Cowork Luna", "Eventos Mar"]
    assert _names({"sort": "nombre"}) == ["Agencia Sol", "Cowork Luna", "Eventos Mar"]


def test_filtros_basicos(companies):
    eventos = Category.objects.get(slug="eventos")
    gracia = Zone.objects.get(slug="gracia")
    assert _names({"score": "60"}) == ["Agencia Sol"]
    assert _names({"category": eventos.pk}) == ["Eventos Mar"]
    assert _names({"zone": gracia.pk}) == ["Eventos Mar"]
    assert _names({"confidence": "55"}) == ["Agencia Sol", "Cowork Luna"]
    assert _names({"catalan": "requerido"}) == ["Eventos Mar"]
    assert _names({"catalan": "no_requerido"}) == ["Agencia Sol", "Cowork Luna"]
    assert _names({"q": "luna"}) == ["Cowork Luna"]
    assert _names({"q": "corporativos"}) == ["Agencia Sol", "Eventos Mar"]  # busca en servicios


def test_filtros_de_seguimiento(companies):
    Favorite.objects.create(company=companies["c"])
    Visit.objects.create(company=companies["b"], status=Visit.Status.CV_DELIVERED)
    assert _names({"favorites": "on"}) == ["Cowork Luna"]
    assert _names({"status": "cv_entregado"}) == ["Eventos Mar"]
    assert _names({"status": "sin_estado"}) == ["Agencia Sol", "Cowork Luna"]


def test_abierto_ahora(companies, monkeypatch):
    companies["a"].opening_hours = "Mo-Fr 09:00-18:00"
    companies["a"].save()
    companies["b"].opening_hours = "Sa 10:00-12:00"
    companies["b"].save()
    tuesday_morning = timezone.make_aware(datetime(2026, 9, 22, 10, 0))
    monkeypatch.setattr(filters_module.timezone, "localtime", lambda: tuesday_morning)
    # "Cowork Luna" no publica horario: cuenta el de oficina estimado (abierto un martes a las 10).
    assert _names({"open_now": "on"}) == ["Agencia Sol", "Cowork Luna"]


def test_parametros_invalidos_no_rompen(companies):
    form = CompanyFilter({"score": "999", "zone": "abc"})
    assert not form.is_valid()
    assert len(list(form.apply())) == 3
    assert form.active_count == 0


def test_explorar_pagina_y_carga_mas_por_htmx(auth_client, monkeypatch):
    monkeypatch.setattr("apps.companies.views.PAGE_SIZE", 2)
    for i in range(5):
        _company(f"Empresa {i}", 90 - i)

    resp = auth_client.get(reverse("explorar"))
    assert resp.status_code == 200
    assert "5 resultados" in resp.text
    assert "Empresa 0" in resp.text
    assert "Empresa 2" not in resp.text
    assert 'id="load-more"' in resp.text

    resp = auth_client.get(
        reverse("explorar") + "?page=2", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="load-more"
    )
    assert "<html" not in resp.text  # parcial
    assert "Empresa 2" in resp.text
    assert "Empresa 0" not in resp.text
    assert "page=3" in resp.text


def test_explorar_filtra_por_htmx_y_conserva_la_consulta(auth_client, companies):
    resp = auth_client.get(
        reverse("explorar") + "?q=sol", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="company-list"
    )
    assert "<html" not in resp.text
    assert 'id="company-list"' in resp.text
    assert "Agencia Sol" in resp.text
    assert "Eventos Mar" not in resp.text


def test_explorar_sin_resultados(auth_client, companies):
    resp = auth_client.get(reverse("explorar") + "?q=zzz")
    assert "Nada con estos filtros" in resp.text


def test_ficha_muestra_datos_y_acciones(auth_client, companies):
    company = companies["a"]
    company.phone = "+34 930 00 00 00"
    company.address = "Carrer de Pujades 51"
    company.save()
    Enrichment.objects.filter(company=company).update(
        summary="Agencia creativa.",
        extracted_at=timezone.now(),
        hook="Me encantó vuestra campaña.",
        fit_reason="Encaja por eventos.",
    )

    resp = auth_client.get(reverse("ficha", args=[company.pk]))
    assert resp.status_code == 200
    html = resp.text
    assert "Agencia creativa." in html
    assert "Me encantó vuestra campaña." in html
    assert 'href="tel:+34930000000"' in html
    assert "google.com/maps/search" in html
    assert "Horario de oficina estimado" in html
    assert "CV entregado" in html  # botones de estado
    assert 'name="next_action"' in html


def test_ficha_de_empresa_inexistente(auth_client, db):
    assert auth_client.get(reverse("ficha", args=[999])).status_code == 404


def test_datos_del_mapa_con_filtros(auth_client, companies):
    Company.objects.filter(pk=companies["a"].pk).update(lat=41.39, lng=2.17)
    Company.objects.filter(pk=companies["b"].pk).update(lat=41.40, lng=2.16)
    Favorite.objects.create(company=companies["a"])

    data = auth_client.get(reverse("mapa_datos")).json()
    assert [p["name"] for p in data["points"]] == ["Agencia Sol", "Eventos Mar"]  # Luna sin coords
    first = data["points"][0]
    assert first["favorite"] is True
    assert first["score"] == 85
    assert first["url"] == reverse("ficha", args=[companies["a"].pk])

    data = auth_client.get(reverse("mapa_datos") + "?score=60").json()
    assert [p["name"] for p in data["points"]] == ["Agencia Sol"]
    assert auth_client.get(reverse("mapa")).status_code == 200


def test_enlaces_de_maps_y_telefono(db):
    company = Company(name="Sol", address="Pujades 51", phone="93 000 00 00")
    assert "Sol%2C+Pujades+51%2C+Barcelona" in company.maps_url
    assert company.tel_url == "tel:930000000"
    assert Company(name="X").maps_url == ""
    assert Company(name="X").tel_url == ""
