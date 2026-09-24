# ADR 0012 · Rutas a pie: planificador propio y enlace de Google Maps

**Estado:** aceptada · 2026-09-24

## Contexto
La fase 6 pide elegir día y zona y recibir 6-10 empresas abiertas en esa franja, priorizando
favoritas y encaje, ordenadas por cercanía, con enlace de Google Maps con paradas y un "modo
ruta" para ir marcando. Coste 0 €: nada de APIs de rutas de pago (Directions, Routes).

Google Maps URLs (documentación, septiembre de 2026): `dir/?api=1` con `origin`, `destination`,
`waypoints` separados por `|` y `travelmode=walking`; **hasta 9 paradas intermedias, pero solo 3
en navegadores móviles**; sin `origin`, sale de la ubicación actual.

## Decisión
- **Planificador propio** (`apps/routes/planner.py`), sin API externa:
  - Candidatas: activas, con coordenadas, en la zona (o toda Barcelona), abiertas en algún
    momento de la franja ese día de la semana (horario OSM o de oficina estimado, ADR 0011) y
    no resueltas (CV entregado, visitada o descartada).
  - Prioridad = favorita (alta 45 / media 30 / baja 15) + 0,6 × encaje (30 si aún no hay
    puntuación) + estado "volver" (20) o "planificado" (10) + próxima acción ese día (25) +
    confianza / 20.
  - Se toman las `size` (6-10) con más prioridad **descontando 12 puntos por km** desde el punto
    de salida (probado en producción: sin descuento se colaba una parada a 38 min del resto) y
    se ordenan por **vecino más cercano** y mejora **2-opt**: óptimo o casi con ≤ 10 paradas.
  - Distancias en línea recta (haversine); el tiempo a pie se estima con 75 m/min y un factor
    1,25 porque las calles no son rectas. Es una estimación, no navegación.
- **Punto de salida**: la ubicación actual si se permite (validada dentro del área de Barcelona)
  o el centro de la zona.
- **Enlaces**: uno con todas las paradas (hasta 9 intermedias + destino, sale de la ubicación
  actual) para la app de Google Maps y escritorio, y **tramos de 3 paradas intermedias** para
  navegadores móviles, cada uno saliendo de la última parada del anterior.
- **Proponer una ruta no cambia el seguimiento**: generar varias para comparar no debe ensuciar
  los estados. Solo las marcas del modo ruta lo hacen ("CV entregado" → CV entregado,
  "Visitada" → visitado, "Cerrada" → volver + nota, "Saltada" → nada) y "Deshacer" restaura el
  estado exacto anterior (`RouteStop.previous_status`).

## Actualización (ADR 0014)
Las rutas admiten hasta **20 paradas** y se editan a mano: añadir desde el mapa o la ficha,
quitar, arrastrar para reordenar y "Ordenar por cercanía". Con más de 10 paradas no hay un
enlace único de Google Maps: se abre por tramos.

## Consecuencias
- Rutas instantáneas, deterministas y testeables, a coste 0.
- Si una empresa sin horario publicado cierra en esa franja, se verá al pasar: "Cerrada" la deja
  en "volver" y con nota.
