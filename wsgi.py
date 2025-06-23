import os
from django.core.wsgi import get_wsgi_application
from whitenoise import WhiteNoise
from pathlib import Path

from base_settings import MEDIA_ROOT

media_root_name = MEDIA_ROOT.name



os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'prod_settings')

application = get_wsgi_application()
application = WhiteNoise(application, root=os.path.join(os.path.dirname(__file__), 'staticfiles'))
application.add_files(os.path.join(os.path.dirname(__file__), media_root_name)) # , prefix='data/')
