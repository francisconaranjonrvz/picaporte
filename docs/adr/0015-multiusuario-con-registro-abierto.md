# ADR 0015 · Multiusuario con registro abierto

**Estado:** aceptada · 2026-09-25 · sustituye en parte al ADR 0004

## Contexto
Picaporte nació para una sola persona (ADR 0004): perfil, puntuaciones, favoritas, visitas,
notas, rutas y ofertas eran globales. Se pide un registro, y de las opciones planteadas
(multiusuario con código de invitación, multiusuario abierto o solo una página de alta con
datos compartidos) se eligió **multiusuario abierto**: cualquiera con el enlace crea su cuenta
y tiene su propia búsqueda.

## Decisión
- **Qué es común y qué es de cada cuenta.**
  - **Común**: el catálogo de empresas y lo que se lee de sus webs (`Company`,
    `SourceRecord`, `Enrichment`, `CompanyPage`). La web de una empresa es la misma para
    todos, y leerla una vez ahorra cuota.
  - **De cada cuenta**: `Profile`, `FitScore` (la nota, su desglose, la justificación y el
    gancho, que salen de `Enrichment`), `Favorite`, `Visit`, `Note`, `Route` y `JobOffer`.
    Todos llevan `user` y, donde toca, son únicos por `(user, company)` o `(user, url)`.
- **Migración de datos**: las filas existentes pasan a la cuenta principal, que es el primer
  superusuario, es decir, la que crea `ensure_user` (`apps/accounts/migration_utils.py`). Las
  puntuaciones se copian de `Enrichment` a `FitScore` antes de borrar esos campos.
- **Lectura por usuario**: `apps/companies/personal.py`.
  - `for_user(qs, user)` anota `fit`, `visit_status` e `is_favorite` para filtrar y ordenar en
    SQL.
  - También precarga las filas del usuario, que las plantillas leen como `company.score`,
    `company.visit` y `company.favorite`.
- **Registro** (`/registro/`): `UserCreationForm` con los validadores de contraseña de Django,
  un campo trampa oculto contra bots y un tope de cuentas (`MAX_USERS`, 50 por defecto; con 0
  queda cerrado). Las cuentas nuevas no son staff.
- **Trabajos en Actions**:
  - Descubrir y enriquecer todo el catálogo son trabajos globales: la app solo deja lanzarlos a
    staff; también corren con el cron.
  - La búsqueda personalizada es de cada cuenta (`JobRun.user`, input `user_id`). Tiene un
    grupo de concurrencia por usuario y un máximo de 5 al día.
  - El descubrimiento busca los sectores opcionales de todas las cuentas.
  - El enriquecimiento nocturno puntúa todos los perfiles con datos.
- **Concurrencia**: como pueden coincidir búsquedas de varias cuentas, el descubrimiento y la
  lectura de webs usan *advisory locks* de Postgres (`apps/core/locks.py`).
  - El descubrimiento espera su turno.
  - La lectura de webs se salta si otro proceso ya la está haciendo, y ese proceso solo puntúa.
- **Cuota de IA**: el análisis del CV (síncrono, en la request) tiene un máximo de 10 al día
  por cuenta (`Profile.parses_today`).

## Consecuencias
- Cada cuenta ve solo su ranking, su seguimiento, sus rutas y sus ofertas; hay tests que lo
  comprueban.
- Con registro abierto, cada cuenta gasta cuota gratuita de NVIDIA y minutos de Actions al
  puntuar su perfil (del orden de 1 llamada por cada 10 empresas). Los topes lo acotan, pero
  con muchas cuentas el enriquecimiento nocturno tardará más en ponerse al día.
- No hay recuperación de contraseña por correo (no se envían emails): hay que cambiarla desde
  el admin o con `ensure_user --reset-password`.
- No hay verificación de email ni captcha. Si hubiera abuso, se cierra el registro con
  `MAX_USERS=0` sin desplegar.
