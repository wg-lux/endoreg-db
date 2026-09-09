import os


def test_pytest_uses_test_settings_module() -> None:
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "endoreg_db.config.settings.test"
