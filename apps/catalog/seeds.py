"""Valores iniciales del catálogo (los siembra la migración 0002 y los usa el admin/tests)."""

CATEGORIES = [
    ("publicidad", "Agencias de publicidad"),
    ("eventos", "Agencias de eventos"),
    ("diseno", "Estudios de diseño"),
    ("comunicacion", "Comunicación y RRPP"),
    ("productoras", "Productoras"),
    ("marketing-digital", "Marketing digital"),
    ("marcas", "Departamentos de marketing de marcas"),
    ("coworkings", "Coworkings creativos"),
]

# bbox = [lng_min, lat_min, lng_max, lat_max] aproximados; se afinan en la fase 3.
ZONES = [
    ("poblenou", "22@ / Poblenou", [2.185, 41.390, 2.215, 41.412]),
    ("eixample", "Eixample", [2.140, 41.380, 2.185, 41.410]),
    ("gracia", "Gràcia", [2.145, 41.397, 2.175, 41.420]),
    ("sant-marti", "Sant Martí", [2.180, 41.395, 2.230, 41.430]),
    ("ciutat-vella", "Ciutat Vella", [2.165, 41.372, 2.192, 41.392]),
    ("sants", "Sants", [2.115, 41.365, 2.150, 41.385]),
]
