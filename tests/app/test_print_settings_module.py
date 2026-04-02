from django.conf import settings


def test_print_settings_module():
    assert settings.SETTINGS_MODULE == "endoreg_db.config.settings.test"
