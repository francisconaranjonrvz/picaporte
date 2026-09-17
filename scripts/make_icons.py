"""Genera los PNG de la PWA a partir de los SVG de static/icons (resvg-py, sin Cairo).

Uso: uv run python scripts/make_icons.py
Los PNG resultantes se versionan; solo hay que regenerarlos si cambia el SVG.
"""

from pathlib import Path

import resvg_py

ICONS_DIR = Path(__file__).resolve().parents[1] / "static" / "icons"

# (svg de origen, nombre de salida, tamaño en px)
TARGETS = [
    ("icon.svg", "icon-192.png", 192),
    ("icon.svg", "icon-512.png", 512),
    ("icon-maskable.svg", "icon-512-maskable.png", 512),
    ("icon.svg", "apple-touch-icon-180.png", 180),
]


def main() -> None:
    for source, output, size in TARGETS:
        svg = (ICONS_DIR / source).read_text(encoding="utf-8")
        png = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
        (ICONS_DIR / output).write_bytes(bytes(png))
        print(f"{output}: {size}x{size}")


if __name__ == "__main__":
    main()
