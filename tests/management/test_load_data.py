from io import StringIO
from django.core.management import call_command
from django.test import TestCase


class LoadDbDataTest(TestCase):
    def test_command_output(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_organ_data", stdout=out)
        call_command("load_contraindication_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        call_command("load_finding_data", stdout=out)