import json
from datetime import date, timedelta

import pytest

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.companies.models import Company
from apps.offers import importers, services
from apps.offers.importers import ImportFormatError, parse
from apps.offers.models import JobOffer, active_offers

TODAY = timezone.localdate()
RECENT = (TODAY - timedelta(days=3)).isoformat()
OLD = (TODAY - timedelta(days=90)).isoformat()

CAREER_OPS = "\n".join(
    [
        "url\tfirst_seen\tportal\ttitle\tcompany\tstatus\tlocation",
        f"https://boards.greenhouse.io/ogilvy/1\t{RECENT}\tgreenhouse-full\tJunior Copywriter\tOgilvy\tadded\tBarcelona",
        f"https://jobs.lever.co/x/2\t{RECENT}\tlever-full\tBackend Dev\tAcme\tskipped_title\tMadrid",
        # Formato largo (12 columnas): la 9 es la fecha de publicación.
        f"https://www.buzzmn.com/talent/3\t{RECENT}\tweb\tBecario de cuentas\tBuzz Agency SL\tadded\tBarcelona\tabc\t{OLD}\t0.9\t\tbuzz",
        f"https://jobs.ashbyhq.com/y/4\t{RECENT}\tashby-api\tDesigner\tEstudio Cactus\tadded\tBarcelona",
        "no-es-url\t2026-09-01\tx\tSin url\tNadie\tadded\t",
        "",
    ]
)


@pytest.fixture
def companies(db):
    return {
        "ogilvy": Company.objects.create(name="Ogilvy", domain="ogilvy.com"),
        "buzz": Company.objects.create(name="Buzz", domain="buzzmn.com"),
        "cactus": Company.objects.create(name="Estudi Cactus"),
        "sol": Company.objects.create(name="Sol"),
    }


def test_parse_career_ops_solo_importa_las_added():
    result = parse("scan-history.tsv", CAREER_OPS.encode())
    assert result.source == "career-ops"
    assert [r.company for r in result.rows] == ["Ogilvy", "Buzz Agency SL", "Estudio Cactus"]
    assert result.skipped == 2  # skipped_title + fila sin URL
    assert result.rows[1].published_on == date.fromisoformat(OLD)  # col. 9 manda
    assert result.rows[0].published_on == date.fromisoformat(RECENT)  # si no, first_seen


def test_parse_csv_en_espanol_con_punto_y_coma_y_json():
    csv_text = (
        "Título;Empresa;Enlace;Fecha;Puntuación\n"
        "Junior PR;Sol;https://sol.es/empleo;2026-09-20;4,5\n"
        ";Sin título;https://x.es;;\n"
    )
    result = parse("ofertas.csv", csv_text.encode("cp1252"))
    assert result.source == "csv"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert (row.title, row.company, row.score) == ("Junior PR", "Sol", 4.5)
    assert result.skipped == 1

    payload = {
        "offers": [
            {"title": "Eventos", "company": "Sol", "url": "https://sol.es/2", "score": "4/5"}
        ]
    }
    result = parse("ofertas.json", json.dumps(payload).encode())
    assert result.source == "json"
    assert result.rows[0].score == 4.0


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "vac"),
        (b"[{", "JSON no v"),
        (b'{"otra": 1}', "lista de ofertas"),
        (b"nombre,ciudad\nSol,BCN", "columnas"),
    ],
)
def test_formatos_invalidos(data, message):
    with pytest.raises(ImportFormatError, match=message):
        parse("x.csv", data)


def test_archivo_demasiado_grande(monkeypatch):
    monkeypatch.setattr(importers, "MAX_BYTES", 10)
    with pytest.raises(ImportFormatError, match="2 MB"):
        parse("x.csv", b"x" * 11)


def test_cruce_por_dominio_nombre_y_parecido(companies):
    matcher = services.CompanyMatcher()
    assert matcher.match("Otra cosa", "https://careers.buzzmn.com/job/1") == (
        companies["buzz"],
        "domain",
    )
    # El dominio de un portal no identifica a la empresa: se cruza por nombre.
    assert matcher.match("Ogilvy", "https://boards.greenhouse.io/ogilvy/1") == (
        companies["ogilvy"],
        "name",
    )
    assert matcher.match("Estudio Cactus", "https://jobs.ashbyhq.com/y") == (
        companies["cactus"],
        "fuzzy",
    )
    assert matcher.match("Sol Consulting", "https://x.es") == (None, "")
    assert matcher.match("Sole", "https://x.es") == (None, "")  # nombres cortos: sin difuso


def test_importar_crea_actualiza_y_cruza(companies):
    stats = services.import_offers(parse("scan-history.tsv", CAREER_OPS.encode()))
    assert (stats.created, stats.updated, stats.matched, stats.skipped) == (3, 0, 3, 2)
    assert len(stats.matched_companies) == 3

    stats = services.import_offers(parse("scan-history.tsv", CAREER_OPS.encode()))
    assert (stats.created, stats.updated) == (0, 3)  # por URL: no duplica
    assert JobOffer.objects.count() == 3

    # Activas: vistas en los últimos 45 días (la de Buzz se publicó hace 90).
    assert set(active_offers().values_list("company__name", flat=True)) == {
        "Ogilvy",
        "Estudi Cactus",
    }
    assert not JobOffer.objects.get(company__name="Buzz").is_active


def test_recruzar_tras_descubrir_empresas(db):
    services.import_offers(parse("scan-history.tsv", CAREER_OPS.encode()))
    assert JobOffer.objects.filter(company__isnull=False).count() == 0
    Company.objects.create(name="Ogilvy")
    assert services.rematch_all() == 1


def test_comando_import_offers(companies, tmp_path, capsys):
    path = tmp_path / "scan-history.tsv"
    path.write_text(CAREER_OPS, encoding="utf-8")
    call_command("import_offers", str(path))
    assert "3 ofertas (3 nuevas" in capsys.readouterr().out


def test_pagina_de_ofertas_importar_y_filtros(auth_client, companies):
    resp = auth_client.get(reverse("ofertas"))
    assert "Aún no hay ofertas" in resp.text

    upload = SimpleUploadedFile(
        "scan-history.tsv", CAREER_OPS.encode(), "text/tab-separated-values"
    )
    resp = auth_client.post(reverse("ofertas_importar"), {"file": upload}, follow=True)
    assert "3 ofertas (3 nuevas) · 3 cruzadas con 3 empresas · 2 descartadas." in resp.text
    assert "Junior Copywriter" in resp.text
    assert "Becario de cuentas" not in resp.text  # antigua: no está entre las activas

    resp = auth_client.get(reverse("ofertas") + "?ver=todas")
    assert "Becario de cuentas" in resp.text

    bad = SimpleUploadedFile("x.json", b"[{", "application/json")
    resp = auth_client.post(reverse("ofertas_importar"), {"file": bad}, follow=True)
    assert "JSON no válido" in resp.text

    resp = auth_client.post(reverse("ofertas_recruzar"), follow=True)
    assert "Cruce actualizado" in resp.text


def test_explorar_y_ficha_muestran_ofertas_activas(auth_client, companies):
    services.import_offers(parse("scan-history.tsv", CAREER_OPS.encode()))
    html = auth_client.get(reverse("explorar") + "?offers=on").text
    assert ">Ogilvy</h2>" in html
    assert ">Estudi Cactus</h2>" in html
    assert ">Buzz</h2>" not in html  # su oferta ya no está activa
    assert ">Sol</h2>" not in html
    assert "Ofertas activas" in html

    html = auth_client.get(reverse("ficha", args=[companies["ogilvy"].pk])).text
    assert "Junior Copywriter" in html
    assert "boards.greenhouse.io/ogilvy/1" in html
