from io import StringIO
from django.core.management import call_command
from django.test import TestCase

from ..patient.conf import TEST_DATALOADER_OUTPUT_PATH


class LoadDbDataTest(TestCase):
    def setUp(self):
        with open(TEST_DATALOADER_OUTPUT_PATH, 'w') as out_file:
            out_file.write("# Test Data Loader\n")
        return super().setUp()

    def test_collect_names(self):
        with open(TEST_DATALOADER_OUTPUT_PATH, 'a') as out_file:
            call_command("load_name_data", stdout=out_file)

    def test_command_output(self):
        with open(TEST_DATALOADER_OUTPUT_PATH, 'a') as out_file:
            call_command("load_gender_data", stdout=out_file)
            call_command("load_unit_data", stdout=out_file)
            call_command("load_disease_data", stdout=out_file)
            call_command("load_event_data", stdout=out_file)
            call_command("load_organ_data", stdout=out_file)
            call_command("load_contraindication_data", stdout=out_file)
            call_command("load_examination_data", stdout=out_file)
            call_command("load_examination_indication_data", stdout=out_file)
            call_command("load_lab_value_data", stdout=out_file)
            call_command("load_finding_data", stdout=out_file)
            call_command("load_medication_data", stdout=out_file)