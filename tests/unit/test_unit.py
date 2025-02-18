# import os

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

from endoreg_db.models import (
    Unit,
)


from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import TEST_UNIT_OUTPUT_PATH


class TestCenter(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_unit_data", stdout=out)

    def test_units_loaded(self):
        units = Unit.objects.all()
        with open(TEST_UNIT_OUTPUT_PATH, "w") as f:
            for unit in units:
                f.write(f"{unit.name}: {unit}\n\n")
