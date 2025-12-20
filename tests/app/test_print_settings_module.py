# tests/debug/test_print_settings.py
from django.conf import settings


def test_print_settings_module():
    print("\nLoaded settings:", settings.SETTINGS_MODULE)
    assert True
