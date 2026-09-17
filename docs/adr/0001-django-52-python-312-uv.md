# ADR 0001 · Django 5.2 LTS, Python 3.12 y uv

**Estado:** aceptada · 2026-09-16

## Contexto
El proyecto se despliega en Vercel (runtime Python) y necesita el mismo conjunto de
dependencias en local (Windows), en CI (Linux) y en producción.

## Decisión
- `Django>=5.2,<5.3` (LTS con soporte de seguridad hasta abril de 2028).
- Python 3.12, fijado en `.python-version` y `requires-python = ">=3.12,<3.13"` (es la
  versión por defecto del runtime de Vercel).
- Dependencias en `pyproject.toml` (`[project]` para producción, `[dependency-groups] dev`
  para herramientas) con `uv.lock` versionado. Vercel instala con `uv sync --frozen` y sin
  el grupo dev; CI y local usan el mismo lock (`uv sync --locked`).

## Consecuencias
- Un único lockfile reproducible; `uv lock --check` en CI detecta locks desactualizados.
- uv debe instalarse en local (`winget install astral-sh.uv` o `py -3.12 -m pip install uv`).
