# Config settings package
import os

# If running under pytest, default to test settings unless explicitly set
if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('DJANGO_SETTINGS_MODULE'):
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.test')
