"""Rastreo educado de la web de una empresa: la home y hasta 4 páginas útiles.

Solo red, sin BD, para poder rastrear varios dominios en paralelo desde el
worker. Reglas:

- `robots.txt` se consulta por dominio antes de cada URL (sin robots.txt -> se
  permite; si el servidor falla -> no se rastrea, por prudencia).
- Máximo `MAX_PAGES` páginas por dominio, `PAGE_DELAY` s entre peticiones al
  mismo dominio, timeout corto, solo HTML y como mucho `MAX_BYTES` por página.
- Solo se guarda el texto visible (recortado), nunca el HTML: Neon Free tiene
  0,5 GB y el texto es lo único que necesita el LLM.
- Emails y redes sociales se extraen de forma determinista (regex y enlaces),
  no con el LLM.
"""

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from apps.companies.http import USER_AGENT

MAX_PAGES = 5
MAX_BYTES = 2_000_000
MAX_PAGE_CHARS = 6_000
PAGE_DELAY = 1.0
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "es,ca;q=0.9,en;q=0.8",
}

# Tipo de página -> palabras clave en la ruta o el texto del enlace (es, ca, en).
# Se clasifica en este orden (lo más específico primero: "Trabaja con nosotros" es
# empleo, no "sobre nosotros") y se elige como mucho una página de cada tipo.
PAGE_KINDS: dict[str, re.Pattern] = {
    "jobs": re.compile(
        r"empleo|trabaj|careers?|jobs?|feina|treball|[uú]nete|join|vacantes|ofertas|talent",
        re.I,
    ),
    "team": re.compile(r"equipo|equip|team|people|persones|personas", re.I),
    "contact": re.compile(r"contact|contacte|contacto|contactar", re.I),
    "about": re.compile(
        r"about|sobre|nosotros|quienes|qui-som|quisom|conoce|coneix|who-we-are|agencia|estudi",
        re.I,
    ),
}
# Orden de rastreo, de más a menos útil (si hay más candidatas que hueco, cae el final).
CRAWL_ORDER = ("about", "jobs", "contact", "team")
SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "x.com": "x",
    "twitter.com": "x",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "vimeo.com": "vimeo",
    "behance.net": "behance",
}
# Una "web" que en realidad es un perfil social o un agregador de enlaces.
NOT_A_SITE = set(SOCIAL_HOSTS) | {"linktr.ee", "linkin.bio", "wa.me", "google.com", "goo.gl"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_JUNK = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|example\.|sentry|wixpress|@2x", re.I)
DROP_TAGS = ("script", "style", "noscript", "svg", "iframe", "template", "form")


class CrawlError(Exception):
    """El sitio no se puede rastrear (sin web real, robots, caído, no es HTML)."""

    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Fetched:
    url: str
    status_code: int
    content_type: str
    content: bytes  # ya descomprimido; BeautifulSoup detecta la codificación

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type


@dataclass
class Page:
    url: str
    kind: str  # home | about | team | jobs | contact
    status_code: int
    text: str
    lang: str = ""


@dataclass
class CrawlResult:
    start_url: str
    final_url: str
    pages: list[Page] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _base_host(host: str) -> str:
    """'blog.agencia.com' -> 'agencia.com' (aproximación suficiente para .com/.es/.cat)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def same_site(url: str, site_host: str) -> bool:
    return _base_host(host_of(url)) == _base_host(site_host)


def normalize_start_url(website: str) -> str:
    website = website.strip()
    if "://" not in website:
        website = f"https://{website}"
    parts = urlsplit(website)
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path or "/", "", ""))


def is_not_a_site(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith(f".{h}") for h in NOT_A_SITE)


def html_to_text(html: str | bytes) -> tuple[str, str, BeautifulSoup]:
    """(texto visible recortado, idioma declarado, soup) de un documento HTML."""
    soup = BeautifulSoup(html, "html.parser")
    lang = ""
    if soup.html is not None:
        lang = (soup.html.get("lang") or "").split("-")[0].lower()[:12]
    for tag in soup(DROP_TAGS):
        tag.decompose()
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    text = "\n".join(line for line in lines if line)
    return text[:MAX_PAGE_CHARS], lang, soup


def find_emails(text: str, soup: BeautifulSoup) -> list[str]:
    found = [
        a["href"][7:].split("?")[0] for a in soup.select('a[href^="mailto:"]') if a.get("href")
    ]
    found += EMAIL_RE.findall(text)
    emails: list[str] = []
    for email in found:
        email = email.strip().lower().rstrip(".")
        if EMAIL_RE.fullmatch(email) and not EMAIL_JUNK.search(email) and email not in emails:
            emails.append(email)
    return emails[:5]


def find_socials(soup: BeautifulSoup) -> dict[str, str]:
    socials: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        host = host_of(a["href"])
        for social_host, network in SOCIAL_HOSTS.items():
            if (host == social_host or host.endswith(f".{social_host}")) and network not in socials:
                socials[network] = a["href"].split("?")[0]
    return socials


def pick_links(soup: BeautifulSoup, base_url: str, site_host: str) -> dict[str, str]:
    """Como mucho un enlace interno por tipo de página (el de ruta más corta)."""
    candidates: dict[str, list[str]] = {kind: [] for kind in PAGE_KINDS}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith("http") or not same_site(url, site_host):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|mp4)$", url, re.I):
            continue
        haystack = f"{urlsplit(url).path} {a.get_text(' ', strip=True)[:60]}"
        for kind, pattern in PAGE_KINDS.items():
            if pattern.search(haystack):
                candidates[kind].append(url)
                break
    home = base_url.rstrip("/")
    chosen: dict[str, str] = {}
    for kind in CRAWL_ORDER:
        urls = [u for u in dict.fromkeys(candidates[kind]) if u.rstrip("/") != home]
        if urls:
            chosen[kind] = min(urls, key=len)
    return chosen


class Crawler:
    """Un rastreo por instancia (un dominio). `client` y `sleep` se inyectan en los tests."""

    def __init__(self, client: httpx.Client | None = None, sleep=time.sleep, delay=PAGE_DELAY):
        self.client = client or httpx.Client(
            headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
        )
        self.sleep = sleep
        self.delay = delay
        self._robots: dict[str, RobotFileParser | None] = {}
        self._requests = 0

    def close(self) -> None:
        self.client.close()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """None = no se puede rastrear el dominio (robots.txt inaccesible por error del servidor)."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                response = self.client.get(f"{origin}/robots.txt", headers={"Accept": "text/plain"})
                if response.status_code >= 500:
                    parser = None
                elif response.status_code >= 400:
                    parser.parse([])  # sin robots.txt: todo permitido
                else:
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return parser is not None and parser.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> Fetched:
        if self._requests:
            self.sleep(self.delay)
        self._requests += 1
        with self.client.stream("GET", url) as response:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            return Fetched(
                url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                content=b"".join(chunks)[:MAX_BYTES],
            )

    def crawl(self, website: str) -> CrawlResult:
        start = normalize_start_url(website)
        if is_not_a_site(start):
            raise CrawlError("not_a_site", f"{host_of(start)} no es una web propia")
        if not self.allowed(start):
            raise CrawlError("robots", "robots.txt no permite rastrear la home")
        try:
            home = self.get(start)
        except httpx.HTTPError as exc:
            raise CrawlError("unreachable", f"{type(exc).__name__}: {exc}"[:300]) from exc
        final_url = home.url
        if is_not_a_site(final_url):
            raise CrawlError("not_a_site", f"la web redirige a {host_of(final_url)}")
        if home.status_code >= 400:
            raise CrawlError("unreachable", f"la home responde {home.status_code}")
        if not home.is_html:
            raise CrawlError("not_html", "la home no es HTML")

        text, lang, soup = html_to_text(home.content)
        result = CrawlResult(start_url=start, final_url=final_url)
        result.pages.append(Page(final_url, "home", home.status_code, text, lang))
        emails = find_emails(text, soup)
        socials = find_socials(soup)

        site_host = host_of(final_url)
        for kind, url in pick_links(soup, final_url, site_host).items():
            if len(result.pages) >= MAX_PAGES:
                break
            if not self.allowed(url):
                continue
            try:
                response = self.get(url)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400 or not response.is_html:
                continue
            page_text, page_lang, page_soup = html_to_text(response.content)
            result.pages.append(
                Page(response.url, kind, response.status_code, page_text, page_lang)
            )
            emails += [e for e in find_emails(page_text, page_soup) if e not in emails]
            for network, link in find_socials(page_soup).items():
                socials.setdefault(network, link)
        result.emails = emails[:5]
        result.socials = socials
        return result


def crawl_site(website: str) -> CrawlResult:
    crawler = Crawler()
    try:
        return crawler.crawl(website)
    finally:
        crawler.close()
