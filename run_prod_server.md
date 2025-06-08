


# Setup Shell Session
```shell
export DJANGO_SETTINGS_MODULE=prod_settings
echo $DJANGO_SETTINGS_MODULE
```

# Setup Database
If not sure, delete prod_sim_db.sqlite3 before beginning to work with a clean state

```shell
export DJANGO_SETTINGS_MODULE=prod_settings
echo $DJANGO_SETTINGS_MODULE
rm prod_sim_db.sqlite3
python manage.py migrate
python manage.py load_base_db_data
python manage.py create_multilabel_model_meta --model_path tests/assets/colo_segmentation_RegNetX800MF_6.ckpt
python manage.py import_video tests/assets/test_nbi.mp4 --segmentation
```

DJANGO_SETTINGS_MODULE=prod_settings python manage.py runserver

```python
from endoreg_db.models import VideoFile
v = VideoFile.objects.first()
v.active_file.url
```

DJANGO_SETTINGS_MODULE=prod_settings gunicorn wsgi:application --bind 0.0.0.0:8000