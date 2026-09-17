# ADR 0003 · Neon: URL pooled en Vercel, directa en Actions; migraciones fuera de Vercel

**Estado:** aceptada · 2026-09-16

## Contexto
Neon Free suspende el compute a los 5 minutos y ofrece dos cadenas de conexión: la pooled
(`-pooler`, PgBouncer en modo transacción) y la directa. Neon recomienda la pooled para
serverless y la directa para migraciones. Vercel no ofrece shell.

## Decisión
- Vercel usa `DATABASE_URL` pooled. `production.py` aplica `CONN_MAX_AGE=0`,
  `CONN_HEALTH_CHECKS=True`, `DISABLE_SERVER_SIDE_CURSORS=True`, `sslmode=require` y
  `connect_timeout=10` (solo si el engine es Postgres; el Postgres de CI no tiene SSL).
- Las migraciones y `ensure_user` se ejecutan en GitHub Actions (`db.yml`) con la URL directa
  guardada como secreto del environment `production`, tras un CI verde en `main` o a mano.
  Nunca en el build de Vercel (solo ve la URL pooled y corre también en previews) ni dentro de
  una request.
- `concurrency: neon-prod-db` reserva el grupo que compartirán los workers de la fase 3.

## Consecuencias
- Ventana breve entre el despliegue de Vercel y la migración: las migraciones deben ser
  aditivas/compatibles hacia atrás.
- La primera ejecución automática de `db.yml` falla hasta que existan los secretos; se
  relanza con `workflow_dispatch`.
