# Solo para desarrollo local (producción: Vercel + Neon, ver README y ADR 0002/0003).
# Mismo Python y mismo uv.lock que CI y Vercel; incluye los grupos dev y worker para poder
# correr tests, `discover` y `enrich` dentro del contenedor.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    # El venv vive fuera de /app: el código se monta como volumen y no debe taparlo.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencias primero (capa cacheada mientras no cambie el lock).
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project --group worker

COPY . .
RUN uv sync --locked --group worker

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
