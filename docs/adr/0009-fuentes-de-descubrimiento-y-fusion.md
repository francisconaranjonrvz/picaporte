# ADR 0009 · Fuentes de descubrimiento (sin Google), deduplicación y trabajos en Actions

**Estado:** aceptada · 2026-09-18

## Contexto
El brief proponía Google Places, Overpass (OSM), Foursquare OS Places y directorios sectoriales.
Verificado contra documentación viva en septiembre de 2026:

- **Google Places API (New)** exige una cuenta de facturación con tarjeta aunque exista cuota
  gratuita (Text Search Enterprise: 1.000 peticiones/mes), y sus términos (3.2.3 "No Scraping /
  No Caching") prohíben almacenar nombres, direcciones o webs de negocios: solo el `place_id`
  indefinidamente y coordenadas 30 días. Guardar empresas en nuestra BD queda fuera de las
  condiciones.
- **Overpass** es gratuito y ODbL; desde abril de 2026 exige un `User-Agent` identificable
  (406 a los genéricos) y pide pausar 30 s tras 429/504. Los filtros por regex sobre áreas
  grandes acaban en 504; los exactos usan índice.
- **Foursquare OS Places** ya no se sirve desde S3 público: se accede por Hugging Face (dataset
  gated con token gratuito) o el portal de Foursquare; ~11,6 GB por release en 100 parquet.

## Decisión
- **Sin Google Places.** Fuentes: OSM (3a), Foursquare OS Places y el censo municipal de locales
  de Open Data BCN (3b). Todas gratuitas y con licencia abierta (ODbL, Apache 2.0, CC-BY 4.0).
- **Interfaz `SourceAdapter` → `RawCompany`** (`apps/companies/sources/`): añadir una fuente es
  una clase con `fetch()`. Cada fuente deja un `SourceRecord` (payload crudo, id externo) y la
  fusión es común.
- **Toda petición HTTP pasa por `apps.companies.http.fetch`**: caché en BD por
  `sha256(método + url + cuerpo)` con TTL (7 días para Overpass) y User-Agent propio.
- **Overpass**: una consulta al bbox de Barcelona con filtros exactos (`office=advertising_agency`,
  `office=coworking`, `amenity=coworking_space`, `office=graphic_design`, `office=marketing`,
  `office=event_management`, `amenity=studio`+`studio=video`…), reintento tras 30 s y un espejo
  de respaldo. Mapeo etiqueta → categoría del catálogo.
- **Deduplicación** (`dedupe.py`): (1) mismo registro de fuente; (2) mismo dominio web
  normalizado (el más cercano si hay varios); (3) nombre normalizado (sin acentos ni sufijos
  legales) con `rapidfuzz.token_set_ratio ≥ 90` a menos de 100 m. **Fusión** campo a campo por
  prioridad de fuente (Foursquare > OSM > censo municipal) con procedencia en `Company.field_sources`;
  una fuente siempre puede refrescar su propio dato.
- **`confidence_score`** (0-100): 15 + 20 por fuente independiente (máx. 3) + web 10 + teléfono 8
  + dirección 6 + coordenadas 6 + categoría 5.
- **Zona** por bbox del catálogo; si varios bbox contienen el punto gana el más pequeño (los
  bbox son aproximados y se solapan; se afinarán con polígonos reales).
- **Trabajos**: `manage.py discover` corre en `discover.yml` (cron lunes 05:00 Europe/Madrid y
  `workflow_dispatch`), en el mismo grupo de concurrencia que las migraciones. Cada ejecución es
  un `JobRun` con métricas, resumen y enlace al run. La app lo lanza con la API de GitHub
  (versión `2026-03-10`, que devuelve el `run_id`) y un token *fine-grained* con permiso
  **Actions: write** (`GITHUB_DISPATCH_TOKEN`); el estado se sondea por HTMX y, si el workflow
  muere antes de arrancar el comando, se cierra el JobRun consultando el run en GitHub.

## Consecuencias
- OSM es parcial (RRPP, eventos y productoras están muy poco etiquetados): en Barcelona da
  ~120 empresas, sobre todo coworkings y agencias de publicidad. La cobertura llega con 3b.
- Los datos guardados (nombre, dirección, web…) proceden de fuentes abiertas; la atribución
  ODbL/Apache 2.0 va en el README.
- Un descubrimiento completo no toca la web: ni latencia ni riesgo en Vercel (ADR 0007).

## Actualización · fase 3b (2026-09-23)

- **Foursquare OS Places**: DuckDB lee `hf://datasets/foursquare/fsq-os-places/release/<última>/`
  sin descargar el volcado; solo baja las columnas y los row groups que pasan el filtro (bbox de
  Barcelona, `date_closed IS NULL`, etiquetas del sector). La consulta tarda **~16 s** en
  Actions. Las categorías se reconocen por palabras clave en la hoja de la etiqueta
  (`… > Advertising Agency`), sin depender de ids. Se descartan los lugares sin refrescar en
  3 años (2/3 del total en Barcelona: cerrados sin marcar). Da ~870 empresas.
  DuckDB va en un grupo `worker` de uv: solo lo instala `discover.yml`, nunca Vercel.
  `HF_TOKEN` (lectura) es secreto del entorno `production`.
- **Directorios sectoriales descartados.** Clutch (Cloudflare) y Páginas Amarillas (Incapsula)
  responden con desafíos anti-bot; Sortlist permite sus listados en `robots.txt`, pero su CDN
  devuelve 403 a cualquier cliente que no sea un navegador (huella TLS), también desde casa.
  Imitar un navegador sería evadir su detección de bots: no se hace.
- **Sustituto: censo municipal de locales en planta baixa** (Open Data BCN, CC-BY 4.0). Ofrece
  nombre, actividad, dirección y coordenadas de ~68.000 locales a pie de calle, justo el tipo de
  sitio donde se entrega un CV en mano. Las actividades son gruesas, así que se filtran dos
  (`Serveis a les empreses i oficines`, `Arts gràfiques`) y se clasifica por palabras clave en el
  nombre del local. Se consulta la **API DataStore de CKAN** (`datastore_search` con filtro de
  actividad y solo 12 columnas, 3 páginas cacheadas): la descarga directa del CSV redirige a un
  desafío anti-bot desde las IPs de GitHub Actions y no se fuerza. El DataStore tiene menos filas
  que el CSV más reciente (~2.500 frente a ~4.000 de esas actividades): ~90 empresas del sector.
- **Ingesta por lotes.** Con ~7 consultas por empresa, 2.700 lugares tardaban más de 30 min
  (Actions en EE. UU. ↔ Neon en Frankfurt). Ahora se cargan empresas y registros una vez, se
  decide todo en memoria (índice por dominio y rejilla de ~200 m) y se escribe con
  `bulk_create`/`bulk_update` cada 500: el descubrimiento completo tarda ~1 min.
- Una respuesta que no es la esperada (p. ej. la página de un desafío anti-bot servida con 200)
  es un error de la fuente, nunca una lectura vacía; y una fuente vacía nunca retira nada.
- **Retirada.** Tras una lectura completa y sin errores de una fuente, sus registros que ya no
  aparecen se borran; una empresa sin ninguna fuente pasa a inactiva (no se borra: puede tener
  favoritos o visitas) y vuelve a activarse si reaparece. Una fuente caída o vacía no retira nada.
- `apps.companies.http.robots_allows()` queda para la fase 4 (visitar webs de empresas).
