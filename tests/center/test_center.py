from endoreg_db.models import (
    Center,
)
    
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_CENTER_OUTPUT_PATH
)

class TestCenter(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)


    def test_center_created(self):
        center = Center.objects.get(name=TEST_CENTER_NAME)
        self.assertEqual(center.name, TEST_CENTER_NAME)

        with open(TEST_CENTER_OUTPUT_PATH, "w") as f:
            f.write(f"{center}\n")
            f.write(f"{center.name}\n")
            

    

