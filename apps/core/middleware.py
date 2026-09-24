"""Middleware propio."""

from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import resolve_url


class HtmxLoginRedirectMiddleware:
    """Sesión caducada en una petición HTMX: se manda al login la página entera.

    Sin esto, el XHR sigue el 302 de LoginRequiredMiddleware y recibe el login con un 200:
    con `hx-swap="none"` la acción se pierde sin aviso y con un swap el formulario de login
    acaba dentro de la tarjeta. Además `next` sería la URL de la acción (solo POST: 405).
    Aquí se responde 204 con `HX-Redirect` al login y `next` = la página que se estaba viendo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.headers.get("HX-Request") != "true"
            or response.status_code != 302
            or urlsplit(response["Location"]).path != resolve_url(settings.LOGIN_URL)
        ):
            return response
        current = urlsplit(request.headers.get("HX-Current-URL", ""))
        next_url = current.path or "/"
        if current.query:
            next_url += f"?{current.query}"
        redirect = HttpResponse(status=204)
        redirect["HX-Redirect"] = (
            f"{resolve_url(settings.LOGIN_URL)}?{urlencode({'next': next_url})}"
        )
        return redirect
