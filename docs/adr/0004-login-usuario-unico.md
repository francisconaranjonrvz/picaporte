# ADR 0004 · Login de usuario único que falla cerrado

**Estado:** aceptada · 2026-09-16 · el registro y el paso a multiusuario están en el ADR 0015

## Decisión
- `LoginView`/`LogoutView` de Django con `LoginRequiredMiddleware` (5.1+): toda vista requiere
  sesión salvo las marcadas con `@login_not_required` (`/health`, `/styleguide`,
  `/manifest.webmanifest`, `/sw.js`, `/offline/`). El admin conserva su propio login.
- `/styleguide` es público a propósito: no expone datos y tiene valor de portfolio.
- El usuario se crea con `manage.py ensure_user` (idempotente, lee `PICAPORTE_USER/PASSWORD/EMAIL`),
  porque `createsuperuser --noinput` falla si el usuario ya existe y no hay shell en Vercel.

## Consecuencias
- Olvidar un decorador nunca deja una página abierta.
- No hay recuperación de contraseña: se cambia con `--reset-password`. El registro abierto
  llegó después (ADR 0015).
