"""Ayudas para las migraciones del paso a multiusuario (ADR 0015)."""


def assign_owner(app_label: str, model_name: str):
    """RunPython: asigna las filas sin usuario a la cuenta principal.

    La cuenta principal es el primer superusuario (la que crea `ensure_user`) o, si no
    hay, el primer usuario. Sin ningún usuario, esas filas no pueden tener dueño y se
    borran (solo pasa en bases de datos de desarrollo vacías de usuarios).
    """

    def forwards(apps, schema_editor):
        Model = apps.get_model(app_label, model_name)
        orphans = Model.objects.filter(user__isnull=True)
        if not orphans.exists():
            return
        User = apps.get_model("auth", "User")
        owner = (
            User.objects.filter(is_superuser=True).order_by("pk").first()
            or User.objects.order_by("pk").first()
        )
        if owner is None:
            orphans.delete()
        else:
            orphans.update(user=owner)

    return forwards
