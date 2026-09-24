from datetime import timedelta
from types import SimpleNamespace

import pytest

from django.utils import timezone

from apps.catalog.models import Category
from apps.companies.models import Company
from apps.enrichment import services
from apps.enrichment.crawler import CrawlError, CrawlResult, Page
from apps.enrichment.models import MAX_EXTRACTION_ATTEMPTS, CompanyPage, Enrichment
from apps.enrichment.profile import profile_fingerprint, stale_scores_count
from apps.enrichment.schemas import CompanyScore, ExtractedCompany, ScoreBatch
from apps.llm.client import LLMError, LLMNotConfigured
from apps.profiles.models import Profile


@pytest.fixture
def profile(user):
    profile = Profile.objects.create(
        user=user,
        full_name="Laura Vidal",
        headline="Graduada en Publicidad y RRPP",
        summary="Me interesan los eventos y la comunicación de marca.",
        skills=["Canva", "Meta Ads"],
        languages=[{"language": "Catalán", "level": "C1"}],
    )
    profile.categories.set(Category.objects.filter(slug="eventos"))
    return profile


def _company(name, website="", category=None, confidence=50, **kw):
    return Company.objects.create(
        name=name,
        website=website,
        category=Category.objects.filter(slug=category).first() if category else None,
        confidence_score=confidence,
        **kw,
    )


def _crawl_result(url="https://buzz.es/"):
    return CrawlResult(
        start_url=url,
        final_url=url,
        pages=[
            Page(url, "home", 200, "Agencia creativa", "es"),
            Page(f"{url}talent", "jobs", 200, "Buscamos becarios", "es"),
        ],
        emails=["hola@buzz.es"],
        socials={"instagram": "https://instagram.com/buzz"},
    )


def _llm(output, model="fake-fast", version="v1"):
    return SimpleNamespace(output=output, call=SimpleNamespace(model=model, prompt_version=version))


EXTRACTED = ExtractedCompany(
    is_company_site=True,
    summary="Agencia creativa de Barcelona.",
    services=["campañas", "eventos"],
    clients=["Wallbox"],
    size_estimate="pequena",
    requires_catalan="desconocido",
    site_languages=["es", "fr"],
    jobs_url="https://buzz.es/talent",
    hiring_note="Buscan becarios.",
)


def test_ensure_enrichments_marca_las_empresas_sin_web(db):
    web = _company("Buzz", "https://buzz.es")
    sin_web = _company("Cowork", "")
    _company("Retirada", "https://x.es", is_active=False)

    assert services.ensure_enrichments() == 2
    assert services.ensure_enrichments() == 0  # idempotente
    assert web.enrichment.crawl_status == "pending"
    assert sin_web.enrichment.crawl_status == "no_website"


def test_seleccion_para_rastrear_prioriza_preferidas_y_confianza(profile):
    a = _company("Agencia", "https://a.es", category="publicidad", confidence=90)
    _company("Eventos", "https://b.es", category="eventos", confidence=40)
    c = _company("Reciente", "https://c.es", category="eventos", confidence=99)
    _company("Sin web", "")
    services.ensure_enrichments()
    Enrichment.objects.filter(company=c).update(crawled_at=timezone.now())
    Enrichment.objects.filter(company=a).update(
        crawled_at=timezone.now() - timedelta(days=45)  # caducada: se vuelve a leer
    )

    selected = services.select_for_crawl(10, profile)

    assert [e.company.name for e in selected] == ["Eventos", "Agencia"]
    assert [e.company.name for e in services.select_for_crawl(1, profile)] == ["Eventos"]


def test_guardar_rastreo_ok_y_fallido(db):
    company = _company("Buzz", "https://buzz.es")
    services.ensure_enrichments()
    enrichment = company.enrichment

    services.save_crawl(enrichment, _crawl_result())
    enrichment.refresh_from_db()
    assert enrichment.crawl_status == "ok"
    assert enrichment.emails == ["hola@buzz.es"]
    assert enrichment.socials == {"instagram": "https://instagram.com/buzz"}
    assert enrichment.needs_extraction
    assert list(company.pages.values_list("kind", flat=True).order_by("kind")) == ["home", "jobs"]

    services.save_crawl(enrichment, CrawlError("robots", "robots.txt no permite"))
    enrichment.refresh_from_db()
    assert enrichment.crawl_status == "robots"
    assert enrichment.crawl_error == "robots.txt no permite"
    assert company.pages.count() == 2  # se conserva lo último bueno


def test_texto_para_la_extraccion_respeta_presupuestos(db, monkeypatch):
    monkeypatch.setattr(services, "DEFAULT_PAGE_CHARS", 5)
    company = _company("Buzz", "https://buzz.es", category="publicidad")
    pages = [
        CompanyPage(
            company=company,
            url="https://buzz.es/c",
            kind="contact",
            status_code=200,
            text="0123456789",
        ),
        CompanyPage(
            company=company, url="https://buzz.es/", kind="home", status_code=200, text="Inicio"
        ),
    ]
    text = services.extraction_input(company, pages)
    assert "Empresa: Buzz" in text
    assert "Agencias de publicidad" in text
    assert text.index('tipo="home"') < text.index('tipo="contact"')  # la home primero
    assert "01234\n</pagina>" in text


@pytest.fixture
def crawled(db):
    company = _company("Buzz", "https://buzz.es", category="publicidad")
    services.ensure_enrichments()
    services.save_crawl(company.enrichment, _crawl_result())
    return company


def test_extraccion_aplica_datos_y_no_admite_urls_inventadas(crawled, monkeypatch):
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return _llm(EXTRACTED)

    monkeypatch.setattr(services, "call_structured", fake_call)
    stats = services.EnrichStats()
    services.extract(stats, services.Deadline(None), workers=1)

    e = Enrichment.objects.get(company=crawled)
    assert stats.extracted == 1
    assert calls[0]["fast"] is True
    assert "Buscamos becarios" in calls[0]["user_text"]
    assert (e.summary, e.size_estimate, e.services) == (
        "Agencia creativa de Barcelona.",
        "pequena",
        ["campañas", "eventos"],
    )
    assert e.site_languages == ["es", "otro"]
    assert e.jobs_url == "https://buzz.es/talent"
    assert (e.extraction_model, e.extraction_prompt_version) == ("fake-fast", "v1")
    assert not e.needs_extraction

    # Segunda pasada: nada que hacer. Con páginas nuevas y una URL inventada, se descarta.
    services.extract(stats, services.Deadline(None), workers=1)
    assert len(calls) == 1
    Enrichment.objects.filter(pk=e.pk).update(pages_hash="otro")
    monkeypatch.setattr(
        services,
        "call_structured",
        lambda **kw: _llm(EXTRACTED.model_copy(update={"jobs_url": "https://buzz.es/inventada"})),
    )
    services.extract(stats, services.Deadline(None), workers=1)
    assert Enrichment.objects.get(pk=e.pk).jobs_url == ""


def test_errores_de_la_ia_en_la_extraccion(crawled, monkeypatch):
    def fail(**kwargs):
        raise LLMError("La IA ha tardado demasiado")

    monkeypatch.setattr(services, "call_structured", fail)
    stats = services.EnrichStats()
    services.extract(stats, services.Deadline(None), workers=1)
    assert stats.llm_errors == 1
    assert Enrichment.objects.get(company=crawled).error == "La IA ha tardado demasiado"

    def not_configured(**kwargs):
        raise LLMNotConfigured("Falta NVIDIA_API_KEY")

    monkeypatch.setattr(services, "call_structured", not_configured)
    stats = services.EnrichStats()
    services.extract(stats, services.Deadline(None), workers=1)
    assert stats.notes == ["IA no configurada: Falta NVIDIA_API_KEY"]


def test_demasiados_errores_seguidos_paran_la_etapa(db, monkeypatch):
    monkeypatch.setattr(services, "MAX_CONSECUTIVE_LLM_ERRORS", 2)
    for i in range(6):
        company = _company(f"E{i}", f"https://e{i}.es")
        services.ensure_enrichments()
        services.save_crawl(company.enrichment, _crawl_result(f"https://e{i}.es/"))
    calls = []

    def fail(**kwargs):
        calls.append(1)
        raise LLMError("saturada")

    monkeypatch.setattr(services, "call_structured", fail)
    stats = services.EnrichStats()
    services.extract(stats, services.Deadline(None), workers=1)
    assert len(calls) == 2  # un bloque (2 x workers) y se para
    assert "Demasiados errores" in stats.notes[-1]


def _scores(*pairs):
    return ScoreBatch(
        scores=[
            CompanyScore(company_id=cid, fit_score=score, reason="motivo", hook="Hola, soy Laura.")
            for cid, score in pairs
        ]
    )


def test_puntuacion_por_lotes_e_ids_desconocidos(profile, monkeypatch):
    a = _company("Eventos Sol", "", category="eventos")
    b = _company("Cowork", "")
    services.ensure_enrichments()
    prompts = []

    def fake_call(**kwargs):
        prompts.append(kwargs["user_text"])
        return _llm(_scores((a.pk, 80), (999, 10)))  # b falta; 999 no existe

    monkeypatch.setattr(services, "call_structured", fake_call)
    stats = services.EnrichStats()
    services.score(profile, stats, services.Deadline(None), workers=1)

    assert stats.scored == 1
    assert "<perfil>" in prompts[0]
    assert "Meta Ads" in prompts[0]
    assert f'"company_id": {a.pk}' in prompts[0]
    ea, eb = Enrichment.objects.get(company=a), Enrichment.objects.get(company=b)
    assert (ea.fit_score, ea.hook, ea.profile_hash) == (
        80,
        "Hola, soy Laura.",
        profile_fingerprint(profile),
    )
    assert eb.fit_score is None  # se reintentará en la próxima ejecución


def test_sin_perfil_no_se_puntua(db, monkeypatch):
    _company("Eventos Sol", "")
    services.ensure_enrichments()
    monkeypatch.setattr(services, "call_structured", lambda **kw: pytest.fail("no debe llamar"))
    stats = services.EnrichStats()
    services.score(None, stats, services.Deadline(None), workers=1)
    assert "perfil está vacío" in stats.notes[0]


def test_cambiar_el_perfil_solo_repite_la_puntuacion(profile, monkeypatch):
    company = _company("Buzz", "https://buzz.es", category="eventos")
    monkeypatch.setattr(services, "crawl_site", lambda url: _crawl_result())
    monkeypatch.setattr(
        services,
        "call_structured",
        lambda **kw: _llm(
            EXTRACTED if kw["output_model"] is ExtractedCompany else _scores((company.pk, 70))
        ),
    )
    stats = services.run_enrichment(mode="all", max_minutes=None, crawl_workers=1, llm_workers=1)
    assert (stats.crawled, stats.extracted, stats.scored) == (1, 1, 1)
    assert stale_scores_count(profile) == 0

    profile.skills = ["Canva", "Meta Ads", "Premiere"]
    profile.save()
    assert stale_scores_count(profile) == 1

    monkeypatch.setattr(services, "crawl_site", lambda url: pytest.fail("no debe rastrear"))
    monkeypatch.setattr(
        services,
        "call_structured",
        lambda **kw: (
            pytest.fail("no debe extraer")
            if kw["output_model"] is ExtractedCompany
            else _llm(_scores((company.pk, 90)))
        ),
    )
    stats = services.run_enrichment(mode="score", max_minutes=None, llm_workers=1)
    assert (stats.crawled, stats.extracted, stats.scored) == (0, 0, 1)
    assert Enrichment.objects.get(company=company).fit_score == 90
    assert stale_scores_count(profile) == 0


def test_las_webs_sin_analizar_esperan_a_la_extraccion(profile, crawled):
    assert services.select_for_scoring(profile_fingerprint(profile)) == []


def test_plazo_agotado_no_empieza_trabajo_nuevo(profile, monkeypatch):
    _company("Buzz", "https://buzz.es")
    monkeypatch.setattr(services, "crawl_site", lambda url: pytest.fail("no debe rastrear"))
    monkeypatch.setattr(services.Deadline, "passed", property(lambda self: True))
    stats = services.run_enrichment(mode="all", crawl_workers=1, llm_workers=1)
    assert stats.crawled == 0
    assert "Tiempo agotado" in stats.notes[0]


def test_ejecucion_concurrente_devuelve_resultados_y_excepciones():
    def fn(x):
        if x == 3:
            raise ValueError("tres")
        return x * 2

    results = dict(services.run_concurrently(fn, [1, 2, 3], workers=3))
    assert results[1] == 2
    assert results[2] == 4
    assert isinstance(results[3], ValueError)


def test_resumen_de_estadisticas():
    stats = services.EnrichStats(crawled=3, extracted=2, scored=5, llm_errors=1)
    stats.crawl_status["ok"] = 2
    assert (
        stats.summary()
        == "webs: 3 rastreadas (2 con contenido)\nIA: 2 analizadas, 5 puntuadas, 1 errores"
    )
    assert stats.as_dict()["crawl_status"] == {"ok": 2}


def test_paginas_con_la_misma_url_no_rompen_el_guardado(db):
    company = _company("Buzz", "https://buzz.es")
    services.ensure_enrichments()
    result = _crawl_result()
    result.pages.append(Page("https://buzz.es/", "about", 200, "duplicada", "es"))

    services.save_crawl(company.enrichment, result)

    assert company.pages.count() == 2
    assert company.pages.get(url="https://buzz.es/").kind == "home"  # gana la primera


def test_un_fallo_al_guardar_no_tumba_el_lote(db, monkeypatch):
    from django.db import IntegrityError

    ok = _company("Bien", "https://bien.es")
    bad = _company("Rara", "https://rara.es")
    services.ensure_enrichments()
    real_save = services.save_crawl

    def flaky_save(enrichment, outcome):
        if enrichment.company_id == bad.pk and isinstance(outcome, CrawlResult):
            raise IntegrityError("duplicate key")
        return real_save(enrichment, outcome)

    monkeypatch.setattr(services, "save_crawl", flaky_save)
    monkeypatch.setattr(services, "crawl_site", lambda url: _crawl_result(url))
    stats = services.EnrichStats()
    services.crawl(services.select_for_crawl(10), stats, services.Deadline(None), workers=1)

    assert stats.crawled == 2
    assert Enrichment.objects.get(company=ok).crawl_status == "ok"
    assert "Error al guardar" in Enrichment.objects.get(company=bad).crawl_error


def test_si_falla_el_guardado_el_error_se_guarda_sin_reusar_la_instancia(db, monkeypatch):
    from django.db import DataError

    company = _company("Rara", "https://rara.es")
    services.ensure_enrichments()
    real_save = Enrichment.save

    def save_that_rejects_socials(self, *args, **kwargs):
        # Como Postgres con un NUL en jsonb: falla mientras la instancia lleve esos datos.
        if self.socials:
            raise DataError("unsupported Unicode escape sequence")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Enrichment, "save", save_that_rejects_socials)
    monkeypatch.setattr(services, "crawl_site", lambda url: _crawl_result(url))
    stats = services.EnrichStats()
    services.crawl(services.select_for_crawl(10), stats, services.Deadline(None), workers=1)

    e = Enrichment.objects.get(company=company)
    assert stats.crawl_status == {"unreachable": 1}
    assert e.crawl_status == "unreachable"
    assert "Error al guardar" in e.crawl_error
    assert e.crawled_at is not None  # no vuelve a ser la primera la noche siguiente


def test_una_extraccion_que_falla_siempre_se_abandona_y_se_puntua(profile, monkeypatch):
    monkeypatch.setattr(services, "MAX_CONSECUTIVE_LLM_ERRORS", 99)
    company = _company("Buzz", "https://buzz.es", category="eventos")
    services.ensure_enrichments()
    services.save_crawl(company.enrichment, _crawl_result())
    calls = []

    def fail(**kwargs):
        calls.append(1)
        raise LLMError("La IA no devolvió un JSON válido tras dos intentos.")

    monkeypatch.setattr(services, "call_structured", fail)
    for _ in range(5):  # cinco noches
        services.extract(services.EnrichStats(), services.Deadline(None), workers=1)
    assert len(calls) == MAX_EXTRACTION_ATTEMPTS
    e = Enrichment.objects.get(company=company)
    assert not e.needs_extraction
    assert services.select_for_scoring(profile_fingerprint(profile)) == [e]

    # Un nuevo rastreo le da otra oportunidad.
    services.save_crawl(e, _crawl_result())
    assert Enrichment.objects.get(company=company).needs_extraction


def test_lote_de_puntuacion_sin_ids_validos_no_queda_en_cache(profile, monkeypatch):
    from apps.llm.models import LLMCall

    company = _company("Eventos Sol", "", category="eventos")
    services.ensure_enrichments()
    call = LLMCall.objects.create(
        purpose=LLMCall.Purpose.SCORING,
        model="fake-fast",
        prompt_version="v1",
        input_hash="x" * 64,
        response={"scores": []},
    )
    monkeypatch.setattr(
        services,
        "call_structured",
        lambda **kw: SimpleNamespace(output=_scores((999, 50)), call=call, cached=False),
    )
    stats = services.EnrichStats()
    services.score(profile, stats, services.Deadline(None), workers=1)

    assert (stats.scored, stats.llm_errors) == (0, 1)
    assert not LLMCall.objects.filter(pk=call.pk).exists()  # la próxima noche se vuelve a pedir
    assert Enrichment.objects.get(company=company).fit_score is None
