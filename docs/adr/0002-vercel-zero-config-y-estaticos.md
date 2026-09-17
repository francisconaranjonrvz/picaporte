# ADR 0002 · Vercel zero-config para Django y estáticos desde el CDN

**Estado:** aceptada · 2026-09-16

## Contexto
Desde abril de 2026 Vercel detecta proyectos Django por `manage.py`, importa los settings en
el build, resuelve `WSGI_APPLICATION` y ejecuta `collectstatic` automáticamente cuando hay
`STATIC_ROOT`, sirviendo `/static/` desde su CDN. El patrón antiguo (`api/index.py` +
`builds`) es legacy.

## Decisión
- `manage.py` en la raíz, entrypoint `config/wsgi.py::application`, `[tool.vercel] entrypoint`
  explícito y `vercel.json` solo con `functions` (maxDuration, excludeFiles) y `regions`.
- `STATIC_ROOT` + WhiteNoise `CompressedManifestStaticFilesStorage`: en Vercel el CDN sirve
  los ficheros con hash; WhiteNoise sigue activo en local y en cualquier otro host.
- El CSS compilado de Tailwind (`static/css/app.css`) se versiona: Vercel no ejecuta Node ni
  el binario de Tailwind. Las fuentes (`assets/tailwind/`) viven fuera de `static/` para
  que el post-procesado del manifest no intente resolver `@import "tailwindcss"`.
- Los settings de producción importan sin abrir la base de datos y solo exigen `SECRET_KEY`
  y `DATABASE_URL`, porque Vercel los importa durante el build.

## Consecuencias
- Cero configuración de rutas; un solo bundle Python (< 500 MB).
- Un `{% static %}` roto solo falla al renderizar: `test_static_manifest.py` simula el build.
- Límite de body de 4,5 MB en Vercel Functions: el CV (fase 2) se limita a 4 MB.
