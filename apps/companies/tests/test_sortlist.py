import json

import pytest

from apps.companies.http import Fetched, FetchError
from apps.companies.sources import sortlist
from apps.companies.sources.base import SourceError
from apps.companies.sources.sortlist import (
    SortlistAdapter,
    agency_to_raw,
    listing_url,
    parse_listing,
)


def address(city_es):
    return {"address": {"es": city_es, "en": city_es}}


def agency(id_, name, cities, website="https://agencia.example"):
    return {
        "id": id_,
        "type": "agency",
        "attributes": {
            "name": name,
            "slug": name.lower().replace(" ", "-"),
            "website": website,
            "website_url": website,
            "tagline": "Hacemos cosas",
            "team_size": 12,
            "reviews_count": 4,
            "addresses": [address(c) for c in cities],
        },
    }


def page_html(agencies, current=0, last=1):
    data = {
        "props": {
            "pageProps": {
                "data": {
                    "organicAgencies": {
                        "data": [],
                        "included": agencies,
                        "meta": {"pagination": {"current": current, "last": last}},
                    }
                }
            }
        }
    }
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(data)}</script></html>"
    )


def test_urls_de_listado():
    assert listing_url("publicidad", 1) == "https://www.sortlist.es/publicidad/barcelona-es"
    assert listing_url("rrpp", 3) == "https://www.sortlist.es/rrpp/barcelona-es?page=3"


def test_parse_listing_devuelve_agencias_y_ultima_pagina():
    html = page_html([agency("a1", "Sol", ["Barcelona, España"]), {"type": "award"}], last=4)
    agencies, last = parse_listing(html)
    assert [a["id"] for a in agencies] == ["a1"]
    assert last == 4


@pytest.mark.parametrize(
    "html",
    [
        "<html>sin datos</html>",
        '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>',
        '<script id="__NEXT_DATA__" type="application/json">no es json</script>',
    ],
)
def test_parse_listing_con_formato_inesperado(html):
    with pytest.raises(SourceError, match="Sortlist"):
        parse_listing(html)


def test_agencia_con_oficina_en_barcelona():
    raw = agency_to_raw(agency("a1", "Sol", ["Madrid, España", "Barcelona, España"]), "rrpp-x")
    assert raw.source == "sortlist"
    assert raw.external_id == "a1"
    assert raw.category_slug == "rrpp-x"
    assert raw.city == "Barcelona"
    assert raw.lat is None
    assert raw.lng is None
    assert raw.website == "https://agencia.example"
    assert raw.payload["cities"] == ["Madrid, España", "Barcelona, España"]
    assert raw.payload["profile"] == "https://www.sortlist.es/agency/sol"


def test_agencia_sin_barcelona_o_sin_nombre_se_descarta():
    assert agency_to_raw(agency("a2", "Luna", ["Madrid, España"]), "publicidad") is None
    assert agency_to_raw(agency("a3", " ", ["Barcelona, España"]), "publicidad") is None


class FakeWeb:
    """Sustituye `fetch` y `robots_allows` del módulo: páginas por URL."""

    def __init__(self, pages, *, cached=False, blocked=()):
        self.pages = pages
        self.cached = cached
        self.blocked = set(blocked)
        self.requested = []

    def fetch(self, url, **kwargs):
        self.requested.append(url)
        status, body = self.pages.get(url, (404, ""))
        return Fetched(status, body, self.cached)

    def robots_allows(self, url):
        return url not in self.blocked


@pytest.fixture
def web(monkeypatch):
    def install(pages, **kwargs):
        fake = FakeWeb(pages, **kwargs)
        monkeypatch.setattr(sortlist, "fetch", fake.fetch)
        monkeypatch.setattr(sortlist, "robots_allows", fake.robots_allows)
        sleeps = []
        monkeypatch.setattr(sortlist.time, "sleep", sleeps.append)
        fake.sleeps = sleeps
        return fake

    return install


def test_recorre_paginas_hasta_el_limite_y_no_repite_agencias(web):
    p1 = page_html([agency("a1", "Sol", ["Barcelona, España"])], last=9)
    p2 = page_html([agency("a2", "Mar", ["Barcelona, España"])], last=9)
    fake = web(
        {
            listing_url("publicidad", 1): (200, p1),
            listing_url("publicidad", 2): (200, p2),
            # a1 otra vez (ya contada); listado de una sola página.
            listing_url("eventos", 1): (
                200,
                page_html([agency("a1", "Sol", ["Barcelona, España"])], last=1),
            ),
        }
    )
    adapter = SortlistAdapter(
        listings={"publicidad": "publicidad", "eventos": "eventos"}, max_pages=2, delay=1.5
    )
    raws = list(adapter.fetch())
    assert [(r.external_id, r.category_slug) for r in raws] == [
        ("a1", "publicidad"),
        ("a2", "publicidad"),
    ]
    assert listing_url("publicidad", 3) not in fake.requested
    assert fake.sleeps == [1.5, 1.5, 1.5]  # una pausa por petición real a la red


def test_desde_cache_no_espera(web):
    fake = web(
        {listing_url("publicidad", 1): (200, page_html([agency("a1", "Sol", ["Barcelona"])]))},
        cached=True,
    )
    list(SortlistAdapter(listings={"publicidad": "publicidad"}).fetch())
    assert fake.sleeps == []


def test_respeta_robots_y_salta_404(web):
    blocked = listing_url("publicidad", 1)
    fake = web({}, blocked=[blocked])
    raws = list(SortlistAdapter(listings={"publicidad": "publicidad", "eventos": "e"}).fetch())
    assert raws == []
    assert blocked not in fake.requested  # prohibido por robots: ni se pide
    assert listing_url("eventos", 1) in fake.requested  # 404: se registra y se sigue


def test_error_http_o_de_red_es_source_error(web, monkeypatch):
    web({listing_url("publicidad", 1): (503, "")})
    with pytest.raises(SourceError, match="503"):
        list(SortlistAdapter(listings={"publicidad": "publicidad"}).fetch())

    def boom(url, **kwargs):
        raise FetchError("timeout")

    monkeypatch.setattr(sortlist, "fetch", boom)
    with pytest.raises(SourceError, match="no responde"):
        list(SortlistAdapter(listings={"publicidad": "publicidad"}).fetch())
