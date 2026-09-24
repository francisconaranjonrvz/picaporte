"""Genera `apps/companies/data/barcelona.json`: el término municipal de Barcelona simplificado.

Descarga de la API de OSM la relación OSM 347950 (límite administrativo, ODbL), une sus
vías exteriores en un anillo y lo simplifica (Douglas-Peucker, ~25 m) para que la
comprobación "¿está dentro de Barcelona?" sea rápida y el fichero pese poco.

    uv run python scripts/barcelona_boundary.py
"""

import json
import math
from pathlib import Path

import httpx

RELATION = 347950
OSM_API = "https://api.openstreetmap.org/api/0.6"
OUT = Path(__file__).resolve().parent.parent / "apps" / "companies" / "data" / "barcelona.json"
TOLERANCE_DEG = 0.00025  # ~25 m


def fetch_ways() -> list[list[tuple[float, float]]]:
    """Vías exteriores de la relación, desde la API de OSM (más fiable que Overpass para esto)."""
    response = httpx.get(
        f"{OSM_API}/relation/{RELATION}/full.json",
        headers={"User-Agent": "picaporte/1.0 (portfolio)"},
        timeout=120,
    )
    response.raise_for_status()
    elements = response.json()["elements"]
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in elements if e["type"] == "node"}
    ways = {e["id"]: e["nodes"] for e in elements if e["type"] == "way"}
    relation = next(e for e in elements if e["type"] == "relation" and e["id"] == RELATION)
    return [
        [nodes[n] for n in ways[m["ref"]]]
        for m in relation["members"]
        if m["type"] == "way" and m.get("role") == "outer"
    ]


def join_rings(ways: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Encadena las vías por sus extremos hasta cerrar anillos."""
    ways = [list(w) for w in ways]
    rings = []
    while ways:
        ring = ways.pop(0)
        while ring[0] != ring[-1]:
            for i, way in enumerate(ways):
                if way[0] == ring[-1]:
                    ring += way[1:]
                elif way[-1] == ring[-1]:
                    ring += way[-2::-1]
                else:
                    continue
                ways.pop(i)
                break
            else:
                raise SystemExit("El límite no cierra: revisa la relación en OSM.")
        rings.append(ring)
    return rings


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    length = math.hypot(x2 - x1, y2 - y1)
    best, index = 0.0, 0
    for i, (x, y) in enumerate(points[1:-1], start=1):
        if length:
            d = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / length
        else:
            d = math.hypot(x - x1, y - y1)
        if d > best:
            best, index = d, i
    if best <= tolerance:
        return [points[0], points[-1]]
    left = simplify(points[: index + 1], tolerance)
    return left[:-1] + simplify(points[index:], tolerance)


def main() -> None:
    rings = join_rings(fetch_ways())
    ring = max(rings, key=len)  # el término municipal es un único polígono
    simple = simplify(ring, TOLERANCE_DEG)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": f"OpenStreetMap relation {RELATION} (ODbL), simplificado ~25 m",
        "ring": [[round(x, 5), round(y, 5)] for x, y in simple],
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(ring)} puntos -> {len(simple)} en {OUT}")


if __name__ == "__main__":
    main()
