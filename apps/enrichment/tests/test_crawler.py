import httpx
import pytest

from apps.enrichment import crawler
from apps.enrichment.crawler import (
    MAX_PAGE_CHARS,
    Crawler,
    CrawlError,
    find_emails,
    html_to_text,
    normalize_start_url,
    pick_links,
    same_site,
)

HOME = """<!doctype html><html lang="es-ES"><head><title>Buzz</title>
<style>.x{}</style><script>var tracking = 1;</script></head><body>
<nav><a href="/es/sobre-nosotros">Nosotros</a> <a href="/es/equipo">Equipo</a>
<a href="/es/talent">Trabaja con nosotros</a> <a href="/contacto">Contacto</a>
<a href="/es/blog">Blog</a> <a href="https://otra.com/about">Fuera</a>
<a href="/dossier.pdf">Dossier</a> <a href="mailto:hola@buzz.es">Escríbenos</a></nav>
<h1>Agencia creativa</h1><p>Campañas para marcas.   Escribe a info@buzz.es.</p>
<a href="https://www.instagram.com/buzz?utm=1">IG</a>
<a href="https://es.linkedin.com/company/buzz">LinkedIn</a>
</body></html>"""
PAGE = '<html lang="ca"><body><p>{}</p><a href="mailto:feina@buzz.es">CV</a></body></html>'


def _client(routes, calls=None):
    calls = calls if calls is not None else []

    def handler(request):
        calls.append(str(request.url))
        status, body, ctype = routes.get(str(request.url), (404, "", "text/html"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _site(extra=None, robots="User-agent: *\nDisallow: /privado\n"):
    routes = {
        "https://buzz.es/robots.txt": (200, robots, "text/plain"),
        "https://buzz.es/": (200, HOME, "text/html; charset=utf-8"),
        "https://buzz.es/es/sobre-nosotros": (200, PAGE.format("Somos 12 personas."), "text/html"),
        "https://buzz.es/es/equipo": (200, PAGE.format("Nuestro equipo."), "text/html"),
        "https://buzz.es/es/talent": (200, PAGE.format("Buscamos becarios."), "text/html"),
        "https://buzz.es/contacto": (200, PAGE.format("Carrer de Pujades 51."), "text/html"),
    }
    routes.update(extra or {})
    return routes


def test_utilidades_de_url():
    assert normalize_start_url("buzz.es") == "https://buzz.es/"
    assert normalize_start_url("http://buzz.es/es?x=1#a") == "http://buzz.es/es"
    assert same_site("https://blog.buzz.es/a", "buzz.es")
    assert not same_site("https://otra.com/a", "buzz.es")


def test_html_a_texto_sin_scripts_ni_estilos_y_recortado():
    text, lang, _ = html_to_text(HOME)
    assert lang == "es"
    assert "Agencia creativa" in text
    assert "tracking" not in text
    assert "  " not in text
    long_text, _, _ = html_to_text("<p>" + "a " * 10_000 + "</p>")
    assert len(long_text) == MAX_PAGE_CHARS


def test_emails_sin_basura():
    text, _, soup = html_to_text(
        '<a href="mailto:Hola@Buzz.es?subject=x">m</a> logo@2x.png info@buzz.es info@buzz.es'
    )
    assert find_emails(text, soup) == ["hola@buzz.es", "info@buzz.es"]


def test_elige_un_enlace_interno_por_tipo():
    _, _, soup = html_to_text(HOME)
    links = pick_links(soup, "https://buzz.es/", "buzz.es")
    assert links == {
        "about": "https://buzz.es/es/sobre-nosotros",
        "jobs": "https://buzz.es/es/talent",  # "Trabaja con nosotros" es empleo
        "contact": "https://buzz.es/contacto",
        "team": "https://buzz.es/es/equipo",
    }
    assert list(links) == ["about", "jobs", "contact", "team"]


def test_rastreo_completo_con_pausas_emails_y_redes():
    sleeps = []
    result = Crawler(client=_client(_site()), sleep=sleeps.append, delay=1.0).crawl("buzz.es")

    assert [p.kind for p in result.pages] == ["home", "about", "jobs", "contact", "team"]
    assert result.pages[0].lang == "es"
    assert result.pages[1].lang == "ca"
    assert "Somos 12 personas." in result.pages[1].text
    assert result.emails == ["hola@buzz.es", "info@buzz.es", "feina@buzz.es"]
    assert "Buscamos becarios." in result.pages[2].text
    assert result.socials == {
        "instagram": "https://www.instagram.com/buzz",
        "linkedin": "https://es.linkedin.com/company/buzz",
    }
    assert sleeps == [1.0] * 4  # una pausa entre peticiones al mismo dominio


def test_robots_prohibe_la_home():
    routes = _site(robots="User-agent: *\nDisallow: /\n")
    calls = []
    with pytest.raises(CrawlError) as exc:
        Crawler(client=_client(routes, calls), sleep=lambda s: None).crawl("https://buzz.es")
    assert exc.value.status == "robots"
    assert calls == ["https://buzz.es/robots.txt"]  # ni siquiera se pide la home


def test_robots_prohibe_una_seccion():
    routes = _site(robots="User-agent: picaporte\nDisallow: /es/equipo\n")
    calls = []
    result = Crawler(client=_client(routes, calls), sleep=lambda s: None).crawl("https://buzz.es")
    assert "team" not in [p.kind for p in result.pages]
    assert "https://buzz.es/es/equipo" not in calls


def test_sin_robots_txt_se_permite_y_con_error_del_servidor_no():
    routes = _site()
    routes["https://buzz.es/robots.txt"] = (404, "", "text/plain")
    assert Crawler(client=_client(routes), sleep=lambda s: None).crawl("buzz.es").pages

    routes["https://buzz.es/robots.txt"] = (503, "", "text/plain")
    with pytest.raises(CrawlError, match="robots"):
        Crawler(client=_client(routes), sleep=lambda s: None).crawl("buzz.es")


@pytest.mark.parametrize(
    ("website", "routes", "status"),
    [
        ("https://instagram.com/buzz", {}, "not_a_site"),
        ("https://buzz.es", {"https://buzz.es/": (500, "", "text/html")}, "unreachable"),
        ("https://buzz.es", {"https://buzz.es/": (200, "%PDF", "application/pdf")}, "not_html"),
    ],
)
def test_errores_de_rastreo(website, routes, status):
    all_routes = {"https://buzz.es/robots.txt": (404, "", "text/plain"), **routes}
    with pytest.raises(CrawlError) as exc:
        Crawler(client=_client(all_routes), sleep=lambda s: None).crawl(website)
    assert exc.value.status == status


def test_redireccion_a_red_social_no_es_una_web():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "buzz.es":
            return httpx.Response(301, headers={"location": "https://linktr.ee/buzz"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(CrawlError) as exc:
        Crawler(client=client, sleep=lambda s: None).crawl("buzz.es")
    assert exc.value.status == "not_a_site"


def test_red_caida_es_unreachable():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("sin red")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CrawlError) as exc:
        Crawler(client=client, sleep=lambda s: None).crawl("buzz.es")
    assert exc.value.status == "unreachable"


def test_paginas_grandes_se_cortan(monkeypatch):
    monkeypatch.setattr(crawler, "MAX_BYTES", 1000)
    big = "<html><body>" + "<p>x</p>" * 5000 + "</body></html>"
    routes = {
        "https://buzz.es/robots.txt": (404, "", "text/plain"),
        "https://buzz.es/": (200, big, "text/html"),
    }
    fetched = Crawler(client=_client(routes), sleep=lambda s: None).get("https://buzz.es/")
    assert len(fetched.content) < len(big)
