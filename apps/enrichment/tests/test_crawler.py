import httpx
import pytest

from apps.enrichment import crawler
from apps.enrichment.crawler import (
    MAX_PAGE_CHARS,
    Crawler,
    CrawlError,
    find_emails,
    find_socials,
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


def test_enlaces_que_acaban_en_la_misma_pagina_no_se_repiten():
    routes = _site()
    # "Sobre nosotros" redirige a la home: no debe guardarse dos veces.
    routes["https://buzz.es/es/sobre-nosotros"] = (200, HOME, "text/html")

    def handler(request):
        url = str(request.url)
        if url == "https://buzz.es/es/sobre-nosotros":
            return httpx.Response(301, headers={"location": "https://buzz.es/"})
        status, body, ctype = routes.get(url, (404, "", "text/html"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    result = Crawler(client=client, sleep=lambda s: None).crawl("https://buzz.es")
    urls = [p.url for p in result.pages]
    assert len(urls) == len(set(urls))
    assert "about" not in [p.kind for p in result.pages]


def test_sin_red_ni_para_robots_es_unreachable_no_robots():
    def handler(request):
        raise httpx.ConnectError("dominio inexistente")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CrawlError) as exc:
        Crawler(client=client, sleep=lambda s: None).crawl("buzz.es")
    assert exc.value.status == "unreachable"
    assert "ConnectError" in str(exc.value)


def test_quita_los_nul_que_postgres_no_admite():
    html = (
        '<html lang="es\x00"><body><p>Hola\x00mundo</p>'
        '<a href="https://instagram.com/ag\x00">IG</a>'
        '<a href="mailto:hola\x00@buzz.es">m</a></body></html>'
    )
    text, lang, soup = html_to_text(html)
    assert "\x00" not in text
    assert lang == "es"
    assert find_socials(soup, "https://buzz.es/") == {"instagram": "https://instagram.com/ag"}
    assert find_emails(text, soup) == ["hola@buzz.es"]


def test_un_enlace_mal_formado_no_aborta_el_rastreo():
    home = HOME.replace(
        "</nav>",
        '<a href="http://[tu-dominio]/empleo">Empleo</a>'
        '<a href="https://[instagram]/x">IG</a></nav>',
    )
    routes = _site({"https://buzz.es/": (200, home, "text/html")})
    result = Crawler(client=_client(routes), sleep=lambda s: None).crawl("buzz.es")
    assert result.pages[0].kind == "home"
    assert result.socials["instagram"] == "https://www.instagram.com/buzz"


def test_redes_sociales_solo_con_enlaces_http():
    _, _, soup = html_to_text(
        '<a href="javascript://instagram.com/%0Aalert(1)">x</a>'
        '<a href="//www.linkedin.com/company/buzz?trk=1">in</a>'
    )
    assert find_socials(soup, "https://buzz.es/") == {
        "linkedin": "https://www.linkedin.com/company/buzz"
    }


def test_subpagina_que_redirige_a_otro_dominio_no_se_guarda():
    routes = _site()

    def handler(request):
        url = str(request.url)
        if url == "https://buzz.es/es/talent":
            return httpx.Response(
                301, headers={"location": "https://www.linkedin.com/company/buzz/jobs"}
            )
        if request.url.host.endswith("linkedin.com"):
            return httpx.Response(
                200,
                text="<p>Inicia sesión</p><p>soporte@linkedin.com</p>",
                headers={"content-type": "text/html"},
            )
        status, body, ctype = routes.get(url, (404, "", "text/html"))
        return httpx.Response(status, text=body, headers={"content-type": ctype})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    result = Crawler(client=client, sleep=lambda s: None).crawl("https://buzz.es")
    assert "jobs" not in [p.kind for p in result.pages]
    assert all("linkedin" not in p.url for p in result.pages)
    assert "soporte@linkedin.com" not in result.emails


def test_el_charset_de_la_cabecera_manda_sobre_el_meta():
    body = '<html><head><meta charset="iso-8859-1"></head><body><p>Diseño</p></body></html>'
    routes = {
        "https://buzz.es/robots.txt": (404, "", "text/plain"),
        "https://buzz.es/": (200, body, "text/html; charset=UTF-8"),
    }
    result = Crawler(client=_client(routes), sleep=lambda s: None).crawl("buzz.es")
    assert result.pages[0].text == "Diseño"


def test_el_corte_no_parte_un_caracter_utf8(monkeypatch):
    head = '<html><head><meta charset="utf-8"><title>Diseño y comunicación</title></head><body>'
    body = (head + "<p>x</p>" * 50 + "<p>ñññ</p></body></html>").encode("utf-8")
    cut = body.index("ñññ".encode()) + 1  # a mitad de la primera "ñ" del final
    monkeypatch.setattr(crawler, "MAX_BYTES", cut)

    def handler(request):
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetched = Crawler(client=client, sleep=lambda s: None).get("https://buzz.es/")
    assert len(fetched.content) == cut - 1
    text, _, _ = html_to_text(fetched.markup)
    assert "Diseño y comunicación" in text


def test_una_descarga_con_cuentagotas_se_corta_por_tiempo_total():
    now = [0.0]

    def drip():
        for _ in range(1000):
            now[0] += 5.0  # un byte cada 5 s: el timeout entre lecturas no salta nunca
            yield b"x"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=drip(), headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CrawlError) as exc:
        Crawler(client=client, sleep=lambda s: None, clock=lambda: now[0]).crawl("buzz.es")
    assert exc.value.status == "unreachable"
    assert now[0] <= crawler.MAX_SECONDS + 10


def test_robots_txt_con_cuentagotas_tambien_se_corta():
    now = [0.0]

    def drip():
        for _ in range(1000):
            now[0] += 5.0
            yield b"#"

    def handler(request):
        return httpx.Response(200, content=drip(), headers={"content-type": "text/plain"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CrawlError) as exc:
        Crawler(client=client, sleep=lambda s: None, clock=lambda: now[0]).crawl("buzz.es")
    assert exc.value.status == "unreachable"
