# ADR 0005 · Tailwind v4 standalone, tokens en CSS y paleta con contraste AA

**Estado:** aceptada · 2026-09-16

## Contexto
El brief pedía "tokens en tailwind.config" y una paleta azul pastel con acción `#5B8DEF`
garantizando contraste AA. Tailwind v4 (v3 está en mantenimiento) define los tokens en CSS
(`@theme`), y el blanco sobre `#5B8DEF` da 3,23:1, por debajo del 4,5:1 que exige AA para
texto normal.

## Decisión
- Tailwind v4.3.3 con el CLI standalone (sin Node). `scripts/tw.py` descarga el binario a
  `.tools/` verificando su sha256 y compila `assets/tailwind/input.css` en
  `static/css/app.css`. Las fuentes de clases son explícitas (`source(none)` + `@source`)
  para que el CSS sea determinista y CI pueda comprobar que está al día.
- Tokens en `assets/tailwind/theme.css` (`@theme`). Se conserva `#5B8DEF` (`action`) para
  iconos, bordes, focus ring y texto grande (3:1 o más) y se añade `#3A6BD0` (`action-fill`,
  5,01:1 con blanco) para botones rellenos y enlaces. Decisión confirmada por la propietaria
  del producto.
- Los pares texto/fondo permitidos están en `apps/core/design.py` y se verifican en tests;
  `/styleguide` los muestra con su ratio.

## Consecuencias
- Cualquier combinación nueva de colores se añade a `PAIRS` y queda comprobada en CI.
- Plus Jakarta Sans se sirve desde `static/fonts/` (OFL) y no desde Google Fonts.
