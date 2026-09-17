#!/usr/bin/env python
"""Utilidad de línea de comandos de Django.

Por defecto usa los settings locales; Vercel y GitHub Actions fijan
DJANGO_SETTINGS_MODULE=config.settings.production por variable de entorno.
"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar Django. ¿Está activado el entorno virtual (uv run ...)?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
