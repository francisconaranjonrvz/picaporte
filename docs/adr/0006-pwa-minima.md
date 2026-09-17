# ADR 0006 · PWA mínima: manifest e iconos, service worker solo para el fallback offline

**Estado:** aceptada · 2026-09-16

## Decisión
- `manifest.webmanifest` y `sw.js` los sirven vistas Django (scope `/`, sin depender de
  `public/` ni de WhiteNoise, que no actúa en Vercel).
- El service worker solo intercepta navegaciones (`request.mode === "navigate"`): red primero
  y, si falla, `/offline/` (página autónoma sin estáticos). No cachea HTML ni parciales HTMX
  para no servir contenido obsoleto. El nombre de la caché lleva la versión del despliegue.
- Sin `hx-boost`: las navegaciones entre pestañas son cargas completas, así el fallback
  offline funciona y `request.htmx` solo es verdadero en peticiones parciales.
- Iconos PNG (192, 512, 512 maskable, apple-touch 180) generados desde SVG con `resvg-py`.

## Consecuencias
- Instalable en Android e iOS; sin modo offline real (llegará, si acaso, con datos propios).
