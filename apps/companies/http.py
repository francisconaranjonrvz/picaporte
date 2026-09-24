"""HTTP con caché en BD: toda petición a una API o web pasa por aquí.

Clave = sha256(método + url + cuerpo). Si hay una respuesta fresca se devuelve
sin tocar la red. Un User-Agent propio e identificable es obligatorio
(Overpass bloquea los genéricos desde 2026).
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from django.utils import timezone

from .models import FetchCache

logger = logging.getLogger(__name__)

USER_AGENT = "picaporte/0.3 (+https://github.com/francisconaranjonrvz/picaporte)"
DEFAULT_TTL = timedelta(days=7)


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Fetched:
    status_code: int
    text: str
    cached: bool

    def json(self):
        import json

        return json.loads(self.text)


def _key(method: str, url: str, body: str) -> str:
    digest = hashlib.sha256()
    for part in (method.upper(), url, body):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _body(data: dict | None) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted((data or {}).items()))


def forget(url: str, *, method: str = "GET", data: dict | None = None) -> None:
    """Borra la respuesta cacheada de una petición (un 2xx que resultó no ser válido)."""
    FetchCache.objects.filter(key=_key(method, url, _body(data))).delete()


def fetch(
    url: str,
    *,
    method: str = "GET",
    data: dict | None = None,
    ttl: timedelta = DEFAULT_TTL,
    timeout: float = 90.0,
    force: bool = False,
    accept: str = "application/json",
) -> Fetched:
    """GET/POST con caché. Solo se cachean respuestas 2xx."""
    body = _body(data)
    key = _key(method, url, body)
    if not force:
        hit = FetchCache.objects.filter(key=key).first()
        if hit is not None and hit.is_fresh:
            return Fetched(hit.status_code, hit.body, True)

    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    try:
        with httpx.Client(headers=headers, timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            response = client.request(method, url, data=data)
    except httpx.HTTPError as exc:
        raise FetchError(f"{method} {url}: {exc}") from exc

    if 200 <= response.status_code < 300:
        FetchCache.objects.update_or_create(
            key=key,
            defaults={
                "url": url[:500],
                "status_code": response.status_code,
                "body": response.text,
                "fetched_at": timezone.now(),
                "expires_at": timezone.now() + ttl,
            },
        )
    return Fetched(response.status_code, response.text, False)


def robots_allows(url: str, *, ttl: timedelta = timedelta(days=1)) -> bool:
    """¿Permite el robots.txt del sitio que nuestro User-Agent pida `url`?

    Sin robots.txt (4xx) se asume permitido, como hacen los buscadores; si el
    servidor falla (5xx o red) se asume prohibido, por prudencia.
    """
    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = fetch(robots_url, ttl=ttl, timeout=20, accept="text/plain")
    except FetchError:
        return False
    if 400 <= response.status_code < 500:
        return True
    if response.status_code != 200:
        return False
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)
