"""ASGI entry point: gunicorn with uvicorn workers (lab.sh selfservice) — async for the live event stream."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "selfservice.settings")
application = get_asgi_application()
