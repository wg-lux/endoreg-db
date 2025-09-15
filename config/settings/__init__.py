# Config settings package
import os

# If running under pytest, force test settings early to avoid .env leakage
if os.environ.get('PYTEST_CURRENT_TEST'):
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.test'
