from datetime import datetime
from types import SimpleNamespace

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
    # Solo el reloj del filtro (parchear django.utils.timezone rompería localdate() en otros sitios).
    monkeypatch.setattr(
        filters_module, "timezone", SimpleNamespace(localtime=lambda: tuesday_morning)
    )
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

    empresa_1 = Company.objects.get(name="Empresa 1")
    assert f"after={empresa_1.pk}" in resp.text  # cursor: la última tarjeta mostrada

    resp = auth_client.get(
        reverse("explorar") + f"?after={empresa_1.pk}",
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="load-more",
    )
    assert "<html" not in resp.text  # parcial
    assert "Empresa 2" in resp.text
    assert "Empresa 0" not in resp.text
    empresa_3 = Company.objects.get(name="Empresa 3")
    assert f"after={empresa_3.pk}" in resp.text


def _load_more(client, query):
    resp = client.get(
        reverse("explorar") + "?" + query, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="load-more"
    )
    return resp, [c.name for c in resp.context["companies"]] if resp.context else []


def test_cargar_mas_no_se_salta_tarjetas_si_la_lista_encoge(auth_client, monkeypatch):
    """Con "Solo favoritas", quitar corazones de la 1.ª tanda no hace saltar la 2.ª."""
    monkeypatch.setattr("apps.companies.views.PAGE_SIZE", 2)
    companies = [_company(f"Empresa {i}", 90 - i) for i in range(5)]
    for company in companies:
        Favorite.objects.create(company=company)

    resp = auth_client.get(reverse("explorar") + "?favorites=on")
    assert [c.name for c in resp.context["companies"]] == ["Empresa 0", "Empresa 1"]
    Favorite.objects.filter(company__in=companies[:2]).delete()  # corazones de la 1.ª tanda

    _, names = _load_more(auth_client, f"favorites=on&after={companies[1].pk}")
    assert names == ["Empresa 2", "Empresa 3"]


def test_cargar_mas_con_cursor_invalido_no_repite_tarjetas(auth_client, companies):
    for after in ("999", "abc", "9" * 40):
        resp, names = _load_more(auth_client, f"after={after}")
        assert resp.status_code == 200
        assert resp.text == ""
        assert names == []


@pytest.mark.parametrize("sort", ["encaje", "confianza", "nombre"])
def test_cargar_mas_recorre_todo_sin_repetir_en_cada_orden(auth_client, monkeypatch, sort):
    monkeypatch.setattr("apps.companies.views.PAGE_SIZE", 2)
    # Empates de encaje, de confianza y de nombre, y empresas sin puntuar.
    for i, (score, confidence) in enumerate([(80, 50), (80, 50), (None, 50), (None, 20), (60, 90)]):
        _company("Igual" if i < 2 else f"Empresa {i}", score, confidence_score=confidence)
    expected = [c.pk for c in CompanyFilter({"sort": sort}).apply()]

    resp = auth_client.get(reverse("explorar") + f"?sort={sort}")
    seen = [c.pk for c in resp.context["companies"]]
    while resp.context and resp.context["next_after"]:
        resp = auth_client.get(
            reverse("explorar") + f"?sort={sort}&after={resp.context['next_after']}",
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="load-more",
        )
        seen += [c.pk for c in resp.context["companies"]]
    assert seen == expected


def test_portada_sin_parametros_ordena_por_encaje(auth_client, companies):
    """GET / sin filtros: el selector dice "Mejor encaje" y el orden debe serlo."""
    _company("Muy segura", None, confidence_score=100)
    resp = auth_client.get(reverse("explorar"))
    names = [c.name for c in resp.context["companies"]]
    assert names == ["Agencia Sol", "Eventos Mar", "Muy segura", "Cowork Luna"]


def test_un_parametro_invalido_no_anula_los_demas(companies):
    eventos = Category.objects.get(slug="eventos")
    eventos.is_active = False
    eventos.save()
    form = CompanyFilter({"q": "luna", "category": eventos.pk, "score": "999"})
    assert [c.name for c in form.apply()] == ["Cowork Luna"]
    assert _names({"q": "x" * 200}) == []  # búsqueda larguísima: se recorta, no invalida
    assert CompanyFilter({"favorites": "on", "zone": "abc"}).active_count == 1


def test_filtrar_por_htmx_actualiza_contador_y_quitar_filtros(auth_client, companies):
    resp = auth_client.get(
        reverse("explorar") + "?score=60&favorites=on",
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="company-list",
    )
    html = resp.text
    assert '<span id="filters-count" hx-swap-oob="true"> · 2</span>' in html
    assert 'id="filters-reset" class="col-span-2" hx-swap-oob="true"' in html

    resp = auth_client.get(
        reverse("explorar") + "?q=sol", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="company-list"
    )
    assert '<span id="filters-count" hx-swap-oob="true"></span>' in resp.text
    assert 'id="filters-reset" class="col-span-2" hidden hx-swap-oob="true"' in resp.text

    page = auth_client.get(reverse("explorar")).text  # página entera: sin OOB
    assert 'id="filters-count"' in page
    assert "hx-swap-oob" not in page


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


@pytest.mark.parametrize(
    ("website", "shown"),
    [
        ("https://sol.com", True),
        ("HTTP://sol.com", True),
        ("www.sol.com", False),  # sería un enlace relativo a /empresa/<pk>/…: 404
        ("javascript:alert(1)", False),
    ],
)
def test_boton_web_de_la_ficha_solo_con_url_http(auth_client, companies, website, shown):
    company = companies["a"]
    company.website = website
    company.save()
    Enrichment.objects.filter(company=company).update(
        socials={"instagram": "javascript://instagram.com/%0aalert(1)"}
    )
    html = auth_client.get(reverse("ficha", args=[company.pk])).text
    assert (f'href="{website}"' in html) is shown
    assert "javascript:" not in html


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
