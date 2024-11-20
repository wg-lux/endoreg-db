from io import StringIO
from django.core.management import call_command
from django.test import TestCase


class LoadDbDataTest(TestCase):
    def test_command_output(self):
        out = StringIO()
        call_command("load_base_db_data", stdout=out)