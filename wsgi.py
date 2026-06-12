import os

from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise  # type: ignore[import-untyped]

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.prod")

application = get_wsgi_application()
application = WhiteNoise(
    application, root=os.path.join(os.path.dirname(__file__), "staticfiles")
)
