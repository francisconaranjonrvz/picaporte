"""Tokens del design system y comprobación de contraste WCAG 2.x.

Los tokens también viven en `assets/tailwind/theme.css` (fuente de verdad
para el CSS); aquí se duplican solo los colores para poder verificar en
tests y mostrar en /styleguide que cada par texto/fondo cumple AA.
"""

from dataclasses import dataclass

TOKENS: dict[str, str] = {
    "bg": "#F5F9FF",
    "surface": "#FFFFFF",
    "primary": "#BFD7FF",
    "action": "#5B8DEF",
    "action-fill": "#3A6BD0",
    "ink": "#1E293B",
    "muted": "#64748B",
    "white": "#FFFFFF",
    "success": "#DCFCE7",
    "success-ink": "#166534",
    "warning": "#FEF3C7",
    "warning-ink": "#92400E",
    "danger": "#FEE2E2",
    "danger-ink": "#991B1B",
}

AA_TEXT = 4.5  # texto normal
AA_UI = 3.0  # texto grande, iconos, bordes, focus


@dataclass(frozen=True)
class Pair:
    fg: str
    bg: str
    minimum: float
    uso: str

    @property
    def fg_hex(self) -> str:
        return TOKENS[self.fg]

    @property
    def bg_hex(self) -> str:
        return TOKENS[self.bg]

    @property
    def ratio(self) -> float:
        return contrast_ratio(TOKENS[self.fg], TOKENS[self.bg])

    @property
    def passes(self) -> bool:
        return self.ratio >= self.minimum


# Pares permitidos en la UI. Cualquier combinación nueva debe añadirse aquí.
PAIRS: list[Pair] = [
    Pair("ink", "bg", AA_TEXT, "Texto principal sobre fondo"),
    Pair("ink", "surface", AA_TEXT, "Texto principal sobre tarjeta"),
    Pair("ink", "primary", AA_TEXT, "Texto sobre chip/pastel"),
    Pair("muted", "surface", AA_TEXT, "Texto secundario sobre tarjeta"),
    Pair("muted", "bg", AA_TEXT, "Texto secundario sobre fondo"),
    Pair("white", "action-fill", AA_TEXT, "Botón primario (relleno)"),
    Pair("action-fill", "surface", AA_TEXT, "Enlace / botón secundario"),
    Pair("action", "surface", AA_UI, "Iconos, bordes, focus ring, texto grande"),
    Pair("success-ink", "success", AA_TEXT, "Badge éxito"),
    Pair("warning-ink", "warning", AA_TEXT, "Badge aviso"),
    Pair("danger-ink", "danger", AA_TEXT, "Badge error"),
]


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)
