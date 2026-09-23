# ADR 0013 · Ofertas: importar career-ops y cruzarlas con las empresas

**Estado:** aceptada · 2026-09-24

## Contexto
La fase 7 pide importar ofertas desde *career-ops* (Indeed, InfoJobs, Tecnoempleo, Jooble,
Jobatus…) "con import JSON/CSV de momento", cruzarlas con `Company` por nombre o dominio y
mostrar "tiene ofertas activas". En career-ops, cada oferta escaneada queda en
`data/scan-history.tsv` (`url, first_seen, portal, title, company, status, location` y columnas
opcionales detrás; la 9 es la fecha de publicación). Las filas `skipped_*` son ofertas filtradas
por título, duplicadas, caducadas o fallidas.

## Decisión
- **Importador con detección de formato** (`apps/offers/importers.py`): la cabecera de
  `scan-history.tsv` activa el lector de career-ops (solo filas `added`); si no, JSON (lista u
  `{"offers": [...]}`) o CSV con cabeceras en inglés o español y `,`/`;`/tab, en UTF-8 o
  Windows-1252 (Excel). Filas sin título, empresa o URL http(s) se descartan y se cuentan.
- **Upsert por URL**: reimportar actualiza sin duplicar. Máximo 2 MB por archivo (el body de
  Vercel admite 4,5 MB); importar en la request es corto (cientos de filas, un índice en memoria).
- **Cruce en tres pasos** (`CompanyMatcher`): dominio de la URL si no es un portal o ATS
  (Greenhouse, Lever, Workday, InfoJobs…) → nombre normalizado exacto → parecido difuso
  (`token_sort_ratio ≥ 92`, solo nombres de 5+ caracteres). Es más estricto que el dedupe porque
  aquí no hay coordenadas que confirmen. "Volver a cruzar" repite el cruce tras descubrir empresas.
- **Oferta activa** = vista o publicada en los últimos 45 días. Explorar tiene el filtro
  "Con ofertas" (un `Exists` anotado en la consulta base) y una insignia; la ficha lista las
  ofertas con enlace al portal.
- También por línea de comandos: `manage.py import_offers ruta/al/scan-history.tsv`.

## Consecuencias
- Probado con un `scan-history.tsv` real (903 filas): 463 ofertas únicas, 102 descartadas y
  un solo cruce, sin falsos positivos; ese historial es de empleo tecnológico, no de agencias.
- La integración "en vivo" con career-ops (sin archivo) queda fuera: el enunciado pide import
  de momento, y career-ops es local (no tiene API).
