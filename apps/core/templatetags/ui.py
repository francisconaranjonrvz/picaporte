"""Componentes del design system como template tags.

`card` y `btn` son `simple_block_tag` (Django 5.2): reciben contenido
anidado ya renderizado. Todo el HTML se construye con `format_html`, de
modo que los atributos procedentes de la plantilla siempre van escapados.
"""

from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()

BTN_BASE = (
    "btn inline-flex items-center justify-center gap-2 font-semibold rounded-2xl "
    "transition duration-150 active:scale-[0.98] focus-visible:outline-none "
    "focus-visible:ring-2 focus-visible:ring-action focus-visible:ring-offset-2 "
    "disabled:opacity-50 disabled:pointer-events-none"
)
BTN_VARIANTS = {
    "primary": "bg-action-fill text-white shadow-soft hover:bg-action-fill/90",
    "secondary": "bg-primary text-ink hover:bg-primary/80",
    "ghost": "bg-transparent text-action-fill hover:bg-primary/40",
    "danger": "bg-danger text-danger-ink hover:bg-danger/80",
}
BTN_SIZES = {
    "sm": "min-h-9 px-3 text-sm",
    "md": "min-h-11 px-4 text-base",  # 44 px: área táctil mínima
    "lg": "min-h-12 px-5 text-base w-full",
}

# Atributos opcionales admitidos por `btn` (nombre en plantilla -> atributo HTML).
BTN_ATTRS = {
    "id": "id",
    "aria_label": "aria-label",
    "hx_get": "hx-get",
    "hx_post": "hx-post",
    "hx_target": "hx-target",
    "hx_swap": "hx-swap",
    "hx_indicator": "hx-indicator",
    "x_on_click": "@click",
    "form": "form",
}


def _attrs(pairs):
    return format_html_join(" ", '{}="{}"', pairs)


@register.simple_block_tag
def card(content, cls=""):
    """Tarjeta base: superficie blanca, rounded-2xl y sombra suave."""
    return format_html('<div class="card {}">{}</div>', cls, content)


@register.simple_block_tag
def btn(
    content,
    variant="primary",
    size="md",
    href=None,
    type="button",
    cls="",
    disabled=False,
    **kwargs,
):
    """Botón (o enlace con aspecto de botón si se pasa `href`)."""
    classes = " ".join([BTN_BASE, BTN_VARIANTS[variant], BTN_SIZES[size], cls]).strip()
    extra = [(BTN_ATTRS[key], value) for key, value in kwargs.items() if key in BTN_ATTRS and value]
    if href:
        pairs = [("class", classes), ("href", href), *extra]
        if disabled:
            pairs.append(("aria-disabled", "true"))
        return format_html("<a {}>{}</a>", _attrs(pairs), content)
    pairs = [("class", classes), ("type", type), *extra]
    if disabled:
        pairs.append(("disabled", "disabled"))
    return format_html("<button {}>{}</button>", _attrs(pairs), content)


@register.simple_tag
def icon(name, cls="size-5"):
    """Icono del sprite inline (templates/components/icons.svg)."""
    return format_html(
        '<svg class="{}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
        cls,
        name,
    )
