"""Candados entre procesos para los workers de Actions (varios workflows a la vez).

Con cuentas abiertas pueden correr a la vez la búsqueda personalizada de dos
usuarios, el descubrimiento semanal y el enriquecimiento nocturno. El índice en
memoria de la ingesta y el rastreo de webs no toleran dos procesos a la vez, así
que se serializan con un *advisory lock* de Postgres (de sesión: se libera solo si
el proceso muere). En SQLite (desarrollo y tests) no hay concurrencia: se concede.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection


def _key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


@contextmanager
def advisory_lock(name: str, *, wait: bool = True) -> Iterator[bool]:
    """Da True si se tiene el candado. Con `wait=False` no espera: da False si está cogido."""
    if connection.vendor != "postgresql":
        yield True
        return
    key = _key(name)
    with connection.cursor() as cursor:
        if wait:
            cursor.execute("SELECT pg_advisory_lock(%s)", [key])
            acquired = True
        else:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
