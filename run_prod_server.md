DJANGO_SETTINGS_MODULE=prod_settings python manage.py migrate

DJANGO_SETTINGS_MODULE=prod_settings python manage.py load_base_db_data

DJANGO_SETTINGS_MODULE=prod_settings python manage.py shell
```
from tests.helpers.data_loader import load_default_ai_model
load_default_ai_model()
exit()
```
DJANGO_SETTINGS_MODULE=prod_settings python manage.py import_video tests/assets/test_nbi.mp4

DJANGO_SETTINGS_MODULE=prod_settings python manage.py  shell

DJANGO_SETTINGS_MODULE=prod_settings gunicorn wsgi:application --bind 0.0.0.0:8000