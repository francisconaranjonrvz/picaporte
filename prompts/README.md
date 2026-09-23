# Prompts versionados

Cada prompt es un fichero `<nombre>_v<N>.md`; el nombre del fichero es la versión.
Cambiar un prompt = crear `…_v2.md`: el hash de caché de `LLMCall` incluye la
versión, así que la nueva llama a la API y la antigua sigue siendo reproducible.

| Prompt | Uso | Guarda la versión en |
|--------|-----|----------------------|
| `cv_parse_v1` | Parseo del CV a perfil (fase 2) | `Profile.parsed_prompt_version` |
| `enrich_extract_v1` | Datos de la empresa a partir de su web (fase 4) | `Enrichment.extraction_prompt_version` |
| `enrich_score_v1` | Encaje perfil × empresa, justificación y gancho (fase 4) | `Enrichment.scoring_prompt_version` |

El esquema JSON de salida lo aporta el modelo Pydantic correspondiente
(`apps/profiles/schemas.py`, `apps/enrichment/schemas.py`); el proveedor NVIDIA
lo añade al final del system prompt.
