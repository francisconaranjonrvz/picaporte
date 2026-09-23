# ADR 0011 · UI principal: filtros en el servidor, HTMX, mapa y seguimiento

**Estado:** aceptada · 2026-09-24

## Contexto
La fase 5 pide Explorar con filtros (encaje, categoría, barrio, estado, abierto ahora, catalán,
confianza), Mapa, ficha de empresa, Favoritas con prioridad y reordenación, y un tracker de
estados y notas. Uso desde el móvil, caminando, con acciones de un toque. Sin SPA ni Node.

## Decisión
- **Un solo formulario de filtros** (`apps/companies/filters.py`, `forms.Form`) que valida los
  parámetros GET y construye el queryset; lo usan Explorar y los datos del Mapa. Parámetros
  inválidos no rompen: se ignoran. Orden por defecto: encaje (nulos al final), luego confianza.
- **HTMX para todo lo parcial**: el formulario hace `hx-get` al cambiar (con `hx-push-url`, así la
  URL filtrada se puede compartir o recargar) y sustituye `#company-list`; "Cargar más" pide la
  página siguiente y se reemplaza a sí mismo. Las acciones de un toque (corazón, estado, notas,
  seguimiento, prioridad) son `hx-post` que devuelven el fragmento actualizado y un
  `HX-Trigger` con el toast. Sin JavaScript propio salvo Leaflet y SortableJS.
- **"Abierto ahora"**: solo OSM publica horarios (`opening_hours`). Un intérprete propio del
  subconjunto habitual (días, franjas, `off`, `24/7`, reglas que se sustituyen) y, para el resto,
  un **horario de oficina estimado** (L-V 9:30-14:00 y 15:30-18:30) marcado con asterisco y
  explicado en la ficha. Se evalúa en Python (≤ 1.000 empresas) porque no es expresable en SQL.
- **Mapa con Leaflet 1.9.4** y teselas de OpenStreetMap con atribución; marcadores circulares
  coloreados por encaje (sin imágenes), favoritas con borde; como mucho 600 puntos; mismos
  filtros que Explorar. Botón "mi ubicación" con la API de geolocalización del navegador.
- **Seguimiento en `apps/tracking`**: `Favorite` (prioridad, posición, nota), `Visit` (una por
  empresa: estado, fecha, contacto, próxima acción y fecha) y `Note` (historial; cada cambio de
  estado deja una nota). Las fechas de próxima acción alimentan "Próximas acciones" en Favoritas.
  Nada se envía a las empresas: todo es local.
- **Reordenar con SortableJS 1.15.7** (el drag & drop nativo de HTML5 no funciona en táctil);
  al soltar se envía el orden completo y el servidor ignora ids ajenos o repetidos.
- **Vendorizado** en `static/vendor/` como Alpine (sin CDN en ejecución) y sin *source maps*: el
  storage manifest de WhiteNoise falla si un fichero referencia uno que no existe (lo detectó el
  test de `collectstatic`).
- El panel de estado de los datos (descubrimiento, análisis) pasa a **`/datos/`**: Explorar queda
  para la lista.

## Consecuencias
- Cada interacción es una petición corta a Vercel (sin procesos largos, ADR 0007).
- El "abierto" estimado puede fallar con empresas de horario atípico; por eso se marca como tal.
- Las notas usan `-id` como desempate de orden: en Windows el reloj tiene ~15 ms de resolución
  y dos notas seguidas pueden compartir instante.
