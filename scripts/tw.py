"""Compila el CSS con el binario standalone de Tailwind (sin Node).

Uso:
    python scripts/tw.py build          # assets/tailwind/input.css -> static/css/app.css (minificado)
    python scripts/tw.py watch [--poll] # recompila al guardar (desarrollo)

Descarga el binario de la release fijada a `.tools/` (ignorado por git) y
verifica su sha256 antes de ejecutarlo. Solo usa la biblioteca estándar
para que funcione igual en Windows, en CI (Linux) y sin `uv sync`.
"""

import hashlib
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "v4.3.3"
BASE_URL = f"https://github.com/tailwindlabs/tailwindcss/releases/download/{VERSION}/"

# (sistema, arquitectura) -> (nombre del asset, sha256 publicado en sha256sums.txt de la release)
ASSETS = {
    ("Windows", "AMD64"): (
        "tailwindcss-windows-x64.exe",
        "e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955",
    ),
    ("Linux", "x86_64"): (
        "tailwindcss-linux-x64",
        "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    ),
}

BASE_DIR = Path(__file__).resolve().parents[1]
TOOLS_DIR = BASE_DIR / ".tools"
INPUT = BASE_DIR / "assets" / "tailwind" / "input.css"
OUTPUT = BASE_DIR / "static" / "css" / "app.css"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_binary() -> Path:
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        sys.exit(
            f"Plataforma {key} sin binario fijado. Descarga el asset de {BASE_URL} a .tools/ "
            "y añade su sha256 a ASSETS en scripts/tw.py."
        )
    name, expected = ASSETS[key]
    binary = (
        TOOLS_DIR / f"{name.replace('.exe', '')}-{VERSION}{'.exe' if name.endswith('.exe') else ''}"
    )
    if binary.exists() and sha256(binary) == expected:
        return binary

    TOOLS_DIR.mkdir(exist_ok=True)
    partial = binary.with_suffix(binary.suffix + ".part")
    print(f"Descargando Tailwind {VERSION} ({name})...")
    urllib.request.urlretrieve(BASE_URL + name, partial)
    actual = sha256(partial)
    if actual != expected:
        partial.unlink(missing_ok=True)
        sys.exit(f"sha256 inesperado para {name}: {actual} (esperado {expected}).")
    partial.replace(binary)
    binary.chmod(binary.stat().st_mode | 0o755)
    return binary


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"build", "watch"}:
        print(__doc__)
        return 2
    command, *flags = argv
    binary = ensure_binary()
    args = [str(binary), "-i", str(INPUT), "-o", str(OUTPUT)]
    if command == "build":
        args.append("--minify")
    else:
        args.append("--watch")
        if "--poll" in flags:
            args.append("--poll")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.call(args, cwd=BASE_DIR, env={**os.environ, "NO_COLOR": "1"})


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
