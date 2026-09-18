# ADR 0007 · Nada largo dentro de una request de Vercel

**Estado:** aceptada · 2026-09-16

## Decisión
- `maxDuration` acotado en `vercel.json` actúa como guarda dura (60 s desde el ADR 0008, que
  admite una única llamada corta a la IA iniciada por la usuaria).
- Todo trabajo pesado (descubrimiento multi-fuente, fetch de webs, llamadas a la Anthropic
  API) se ejecuta en GitHub Actions como comandos de gestión contra Neon. La web solo lanza
  workflows vía la API de GitHub (`workflow_dispatch`) y muestra el estado del último `JobRun`.
- Toda respuesta de API o fetch web se cachea en la base de datos para no repetir coste.

## Consecuencias
- Latencia de minutos entre "Buscar nuevas empresas" y ver resultados; aceptable para el uso.
- Hosting a 0 €: Vercel Hobby + Neon Free + minutos gratuitos de Actions.
