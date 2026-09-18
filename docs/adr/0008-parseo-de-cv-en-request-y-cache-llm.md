# ADR 0008 · Parseo del CV con un LLM gratuito dentro de la request, y caché de llamadas

**Estado:** aceptada · 2026-09-17 (proveedor gratuito desde 2026-09-18)

## Contexto
El ADR 0007 prohíbe trabajo largo dentro de una request de Vercel, pensando en el
descubrimiento y el enriquecimiento (minutos, cientos de llamadas). El parseo del CV es una
única llamada a un LLM con un CV de 1-3 páginas (5-15 s), la lanza la usuaria a mano y espera
el resultado en pantalla. El proyecto debe costar 0 €, así que el proveedor por defecto es la
API gratuita de NVIDIA (build.nvidia.com), OpenAI-compatible.

## Decisión
- **Proveedores intercambiables** (`apps/llm/providers/`, `LLM_PROVIDER`): `nvidia` por
  defecto (gratuito, `google/gemma-4-31b-it`) y `anthropic` opcional (de pago, Claude
  Haiku 4.5). La capa común (`apps/llm/client.py`) hace caché y contabilidad; cada proveedor
  solo implementa `complete()`.
- El parseo se ejecuta **en la request** (HTMX + skeleton). `vercel.json` sube `maxDuration`
  a 120 s; el cliente tiene `timeout` de 90 s y **sin reintentos** (un reintento no cabría en
  la ventana). El nivel gratuito de NVIDIA es una cola compartida: el mismo CV tarda entre 16
  y 70 s según el momento. Es la única excepción acotada al ADR 0007: una llamada, iniciada
  por la usuaria.
- **NVIDIA no acepta PDF**: el texto se extrae con `pypdf` (si el PDF es un escaneo sin texto,
  se avisa) y va en el mensaje. La salida se pide con el esquema JSON en el prompt y
  `response_format=json_object` (el endpoint alojado rechaza el `nvext.guided_json` que
  recomienda la documentación de NIM); si el JSON no valida con Pydantic se pide **una**
  corrección al modelo. Elección del modelo tras un benchmark con dos CVs (uno con catalán,
  siglas y secciones desordenadas, puntuado contra una respuesta esperada):
  `google/gemma-4-31b-it` 100/100 en ambas pasadas (~30 s) → por defecto;
  `openai/gpt-oss-20b` 76-88/100 y 16-90 s → alternativa; los modelos de razonamiento
  (GLM 5.3, Nemotron 3 Super, DeepSeek flash) agotan el tiempo o se cortan; varios modelos
  listados por la API no existen para la cuenta (404) y `meta/llama-3.3-70b` está retirado. Con Anthropic el PDF
  viaja como documento y la validación la hace el SDK (structured outputs).
- **Caché en BD** (`apps.llm.LLMCall`): cada llamada se guarda con `sha256(proveedor + modelo +
  versión del prompt + system + texto + esquema de salida + PDF)`. Repetir el análisis del
  mismo CV con el mismo prompt no cuesta nada; cambiar el prompt (`prompts/cv_parse_v2.md`), el
  esquema Pydantic o el proveedor invalida la caché de forma natural. Tokens y coste estimado
  (0 con NVIDIA) quedan registrados para la tabla de costes del README.
- Los prompts viven en `prompts/*_vN.md`; el perfil guarda `parsed_prompt_version` y
  `parsed_model`.
- El parseo **no pisa** lo editado a mano: solo rellena campos vacíos salvo que la usuaria
  marque "Sobrescribir".

## Consecuencias
- Necesita `NVIDIA_API_KEY` en Vercel (o `ANTHROPIC_API_KEY` con `LLM_PROVIDER=anthropic`);
  sin clave la UI lo dice y el resto de la app funciona.
- Coste 0 € con NVIDIA (límite de ~40 peticiones/minuto en el nivel gratuito, de sobra para
  un solo uso). Con Anthropic, ~0,01 USD por CV.
- Un modelo abierto de 70B extrae peor que Claude en CVs con maquetación compleja; la
  extracción de texto pierde el orden de las columnas. La usuaria siempre puede corregir a
  mano, y el proveedor se cambia con una variable de entorno.
- Si en el futuro el parseo creciera (varios documentos, modelos más lentos), pasaría a
  GitHub Actions como el resto de trabajos.
