# ADR 0010 · Enriquecimiento: rastreo educado y dos etapas de IA (extracción y encaje)

**Estado:** aceptada · 2026-09-24

## Contexto
La fase 4 pide leer la web de cada empresa (home + sobre nosotros/contacto/equipo/empleo, máx. 5
páginas, timeout, robots.txt), extraer datos y puntuar el encaje con el perfil usando un LLM con
salida validada, y **recalcular la puntuación al cambiar el perfil sin volver a hacer fetch**.
Restricciones: coste 0 € (LLM gratuito de NVIDIA), Neon Free (0,5 GB), nada largo en Vercel.

Medido en septiembre de 2026 con webs reales de la BD:
- Rastrear una web tarda 0,5-13 s (5 páginas con 1 s de pausa). ~3 de cada 10 sitios prohíben
  a todos los bots en `robots.txt` o no responden.
- El nivel gratuito de NVIDIA es lento y variable: `google/gemma-4-31b-it` y `openai/gpt-oss-20b`
  agotaban 120 s incluso con un "hola"; `nvidia/nemotron-3.5-lightning-30b-a3b` respondía en
  0,6 s y extraía bien **con el razonamiento apagado** (`enable_thinking: false`); con él activo
  gasta los tokens pensando. Aun así, bajo carga, una extracción tarda de 5 s a 2 min.

## Decisión
- **Rastreador propio** (`apps/enrichment/crawler.py`, httpx + BeautifulSoup): `robots.txt` por
  dominio (sin fichero → permitido; error del servidor → no se rastrea), máx. 5 páginas elegidas
  por palabras clave en ruta y texto del enlace (empleo y equipo antes que "sobre nosotros":
  "Trabaja con nosotros" es empleo), 1 s entre peticiones al mismo dominio, timeout 15 s, solo
  HTML y ≤ 2 MB. Una "web" que es un perfil social o un agregador de enlaces no se rastrea.
  Emails y redes sociales se extraen de forma determinista, no con el LLM.
- **Se guarda solo el texto visible** (`CompanyPage`, ≤ 6.000 caracteres por página): es la caché
  del fetch. El HTML no se guarda: 1.000 webs × 5 páginas de HTML no caben en 0,5 GB.
- **Dos etapas de IA con su propio prompt versionado** (`prompts/enrich_extract_v1.md`,
  `prompts/enrich_score_v1.md`) y salida Pydantic (`apps/enrichment/schemas.py`):
  1. *Extracción* (depende solo de la web): resumen, servicios, clientes, tamaño, ¿requiere
     catalán?, idiomas, página de empleo y señales de contratación. Se repite solo si cambia el
     hash de las páginas. La URL de empleo solo se acepta si es una de las páginas rastreadas.
  2. *Puntuación* (perfil + empresa): `fit_score` 0-100 con rúbrica, justificación y un gancho de
     dos frases para presentarse. Va **por lotes de 10 empresas** (una llamada, sin texto de
     páginas) y guarda `profile_hash`. Cambiar el perfil solo repite esta etapa: botón
     "Recalcular encaje" (Explorar y Perfil) → `enrich.yml` con `mode=score`.
- **El contenido web es no confiable**: los prompts lo tratan como material a analizar, nunca como
  instrucciones; la salida pasa por Pydantic (longitudes recortadas, literales cerrados) y nada
  de lo extraído se ejecuta ni se envía a nadie.
- **Modelo de volumen configurable** (`LLM_MODEL_FAST`, por defecto el rápido del proveedor:
  nemotron lightning en NVIDIA, Haiku en Anthropic); el parseo del CV sigue con su modelo.
  Si el modelo devuelve el esquema en vez de los datos, la reparación se lo dice explícitamente.
- **Trabajo en Actions** (`enrich.yml`): cada noche a las 03:30 y a demanda desde la app. Rastreo
  en paralelo por dominios (8 hilos, solo red), IA en un pool de 3 hilos, escrituras en el hilo
  principal. Presupuesto de 45 min y hasta 150 webs por ejecución: al agotarse no se empieza
  trabajo nuevo y la siguiente ejecución continúa (primero las categorías preferidas del perfil
  y las empresas con más confianza). Cinco errores de IA seguidos detienen la etapa.
- **La web no importa nada del worker**: `apps/enrichment/profile.py` (huella del perfil y
  puntuaciones desactualizadas) no depende de `crawler`/`services`; un test lo comprueba, porque
  Vercel no instala BeautifulSoup ni DuckDB.

## Consecuencias
- Coste 0 €, pero el primer enriquecimiento completo (~500 webs) lleva varias noches con el nivel
  gratuito actual; las puntuaciones aparecen de forma progresiva.
- Con `LLM_PROVIDER=anthropic` el mismo código usa Haiku (más rápido, de pago).
- Las empresas sin web también se puntúan (con menos información y la nota topada por la rúbrica),
  porque el censo municipal aporta locales a pie de calle sin web que valen para una visita.
