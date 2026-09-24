# ADR 0014 · Búsqueda personalizada, ranking por criterios y limpieza de fuentes

**Estado:** aceptada · 2026-09-24

## Contexto
Tras las fases 1-7, tres problemas de uso real:

1. **Rellenar el perfil no cambiaba qué se buscaba**: solo servía para puntuar, y había que
   lanzar a mano "Recalcular encaje". La idea es la de un escaneo de career-ops: tu perfil
   define dónde buscar y el resultado es un ranking de encaje.
2. **Mucha basura** en la lista (medida en la BD local: 90 de 200 empresas activas):
   - coworkings (38 %), que no contratan: cadenas (WeWork ×4, Spaces, Regus), bancos
     ("Santander Work Café") y coworkings de belleza o salud;
   - imprentas, copisterías y rotulistas, que entraban por la actividad "Arts gràfiques" del
     censo municipal;
   - falsos positivos por palabra clave en el nombre ("Comunicacions i Software",
     "Disseny d'Interiors", "Press i Car");
   - empresas de L'Hospitalet, Cornellà, Esplugues o El Prat, porque el recuadro de consulta
     es más grande que el término municipal.
3. Las rutas se quedaban en 10 paradas y no se podían editar.

## Decisión
- **Sectores**: los básicos (publicidad, comunicación, marketing digital, diseño, eventos,
  productoras) se buscan siempre. Los **opcionales** (medios, editoriales, fotografía, música,
  cultura) solo si el perfil los marca; tienen reglas en las tres fuentes (etiquetas OSM,
  etiquetas de Foursquare, palabras en el nombre del censo). Coworkings y "marcas" se retiran
  del catálogo (inactivas, no borradas).
- **La IA propone los sectores** al analizar el CV (`cv_parse_v2`, lista cerrada de slugs,
  validada en Pydantic). Solo se aplican si el perfil no tenía sectores o con "Sobrescribir".
- **Al guardar el perfil** se lanza `personalize.yml` si hace falta: sectores distintos de los
  de la última búsqueda (`Profile.discovered_sectors`), busca y puntúa; mismo sector pero
  perfil distinto, solo puntúa. Un único `JobRun` (`personalize`) con las dos fases, en el
  grupo de concurrencia de Neon. Mientras corre no se lanza otro enriquecimiento desde la app.
- **Filtro de relevancia central** (`apps/companies/relevance.py`), común a todas las fuentes:
  sector buscado, **dentro del término municipal** (polígono OSM 347950 simplificado a ~25 m,
  generado por `scripts/barcelona_boundary.py`) y nombre sin señales de otra actividad. Lo
  descartado no llega a la ingesta, así que `retire_unseen` retira lo que ya estaba guardado.
  El resumen del descubrimiento cuenta los descartes por motivo.
- **Ranking por criterios** (`enrich_score_v2`): la IA puntúa cinco criterios con peso fijo
  (sector 40, hueco junior 20, preferencias 20, idiomas 10, información 10) y **la app suma**,
  así los pesos no dependen de que el modelo sume bien. Veredicto por bandas, como las de
  career-ops: ≥ 75 "Ve primero", 60-74 "Merece la pena", 40-59 "Si pasas cerca", < 40 "Baja
  prioridad". El desglose se guarda (`Enrichment.fit_breakdown`) y se ve en la ficha.
- **Rutas**: hasta 20 paradas. Se pueden añadir desde el mapa o la ficha (inserción donde menos
  alarga el paseo y nunca antes de lo ya visitado), quitar, arrastrar para reordenar u "Ordenar
  por cercanía". Con más de 10 paradas, Google Maps se abre por tramos.

## Consecuencias
- La primera búsqueda con los filtros nuevos retira del orden del 45 % de las empresas activas.
  Favoritas, visitas y notas se conservan, porque las empresas pasan a inactivas y no se borran.
  Si se desmarca un sector opcional, sus empresas se retiran en la siguiente búsqueda.
- Cambiar el prompt de puntuación cambia la huella del perfil: todo se vuelve a puntuar, poco a
  poco y dentro del presupuesto de cada ejecución.
- Las reglas por nombre son heurísticas: alguna empresa buena puede caer ("Blueprint Studio"
  no, pero sí un estudio llamado "… Print") y alguna mala colarse. Lo que se cuele puntúa bajo
  en el ranking.
- Las etiquetas exactas de Foursquare para los sectores opcionales se comprueban en el log del
  descubrimiento (recuento de etiquetas) tras la primera ejecución.
