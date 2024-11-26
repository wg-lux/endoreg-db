from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from endoreg_db.models import EmissionFactor


class LoadDbDataTest(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_center_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_green_endoscopy_wuerzburg_data", stdout=out)

    def test_command_output(self):
        emission_factors = EmissionFactor.objects.all()
        emissions = [_ for _ in emission_factors]
        assert emissions