# ADR 0008 · Parseo del CV con Haiku dentro de la request y caché de llamadas LLM

**Estado:** aceptada · 2026-09-17

## Contexto
El ADR 0007 prohíbe trabajo largo dentro de una request de Vercel, pensando en el
descubrimiento y el enriquecimiento (minutos, cientos de llamadas). El parseo del CV es una
única llamada a Claude Haiku 4.5 con un PDF de 1-3 páginas (5-10 s), la lanza la usuaria a
mano y espera el resultado en pantalla.

## Decisión
- El parseo se ejecuta **en la request** (HTMX + skeleton). `vercel.json` sube `maxDuration`
  a 60 s; el cliente Anthropic tiene `timeout` de 45 s y **sin reintentos** (un reintento no
  cabría en la ventana). Es la única
  excepción acotada al ADR 0007: una llamada, modelo rápido, iniciada por la usuaria.
- El PDF se envía **directamente** a la API como documento (`document` base64): no se extrae
  texto en Python, así el modelo ve la maquetación (columnas, iconos, tablas).
- La salida se valida con **structured outputs** (`client.messages.parse` + Pydantic
  `ParsedCV`): el JSON llega con la forma exacta o la llamada falla de forma controlada.
- **Caché en BD** (`apps.llm.LLMCall`): cada llamada se guarda con `sha256(modelo + versión
  del prompt + system + texto + esquema de salida + PDF)`. Repetir el análisis del mismo CV con el mismo prompt no
  cuesta nada; cambiar el prompt (`prompts/cv_parse_v2.md`) o el esquema Pydantic invalida la
  caché de forma natural.
  Tokens y coste estimado quedan registrados para la tabla de costes del README.
- Los prompts viven en `prompts/*_vN.md`; el perfil guarda `parsed_prompt_version` y
  `parsed_model`.
- El parseo **no pisa** lo editado a mano: solo rellena campos vacíos salvo que la usuaria
  marque "Sobrescribir".

## Consecuencias
- Necesita `ANTHROPIC_API_KEY` en Vercel; sin ella la UI lo dice y el resto de la app funciona.
- Un CV de 2 páginas cuesta ~0,005-0,01 USD; el segundo análisis, 0.
- Si en el futuro el parseo creciera (varios documentos, modelos más lentos), pasaría a
  GitHub Actions como el resto de trabajos.
