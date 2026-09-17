"""Entrypoint WSGI.

Vercel lo resuelve desde WSGI_APPLICATION y expone `application` como
única Vercel Function. Si nadie define DJANGO_SETTINGS_MODULE, aquí se
asume producción (cinturón y tirantes frente a un despliegue mal configurado).
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
