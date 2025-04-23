from pathlib import Path
import os
import logging
from endoreg_db.utils.paths import STORAGE_DIR

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("data/tests.log"),
        # logging.StreamHandler()
    ]
)

PATH_LOGGER = logging.getLogger("paths")
RAW_PDF_LOGGER = logging.getLogger("raw_pdf")
PATIENT_LOGGER = logging.getLogger("patient")
DEFAULT_OBJECT_LOGGER = logging.getLogger("default_objects")

PATH_LOGGER.setLevel(logging.WARNING)
RAW_PDF_LOGGER.setLevel(logging.WARNING)
PATIENT_LOGGER.setLevel(logging.INFO)
DEFAULT_OBJECT_LOGGER.setLevel(logging.WARNING)

ASSET_DIR = Path(__file__).parent / "assets"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

DEBUG=True
SECRET_KEY = "fake-key"
INSTALLED_APPS = [
    "tests",
    "endoreg_db.apps.EndoregDbConfig",
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

BASE_DIR = Path(__file__).parent.parent
# logger.info(f"BASE_DIR: {BASE_DIR}")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    },
}

TIME_ZONE = "Europe/Berlin"

# settings.py


MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/' # Adjust if needed

# Ensure your DEFAULT_FILE_STORAGE uses MEDIA_ROOT correctly
# If using default FileSystemStorage, it should work automatically.
# DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'