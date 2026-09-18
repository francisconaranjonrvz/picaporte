"""Carga de prompts versionados desde `prompts/<nombre>_v<N>.md`.

El nombre del fichero es la versión: cambiar el prompt implica crear un
fichero nuevo, y el hash de caché incluye la versión, así que una versión
nueva vuelve a llamar a la API y la antigua sigue siendo reproducible.
"""

from functools import cache
from pathlib import Path

from django.conf import settings

PROMPTS_DIR = Path(settings.BASE_DIR) / "prompts"


@cache
def load_prompt(name: str) -> tuple[str, str]:
    """Devuelve (versión, texto) para `prompts/<name>.md`, p. ej. `cv_parse_v1`."""
    path = PROMPTS_DIR / f"{name}.md"
    return name, path.read_text(encoding="utf-8").strip()
