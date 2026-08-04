from django.core.management import call_command
from django.test import TestCase


class TestDataLoaderComponents(TestCase):
    """
    Test the data loading commands.
    Each test method runs in its own transaction, ensuring isolation.
    The database state is automatically reset after each test by Django's TestCase.
    """

    def test_load_information_source_data(self):
        """
        Test if the load_information_source command runs without errors.
        """
        verbose = False
        call_command("load_tag_data", verbose=verbose)
        call_command("load_information_source", verbose=verbose)

        call_command("load_risk_data", verbose=verbose)

        # Load Center Data
        call_command("load_center_data", verbose=verbose)
        call_command("load_endoscope_data", verbose=verbose)
        call_command("load_distribution_data", verbose=verbose)

        call_command("load_gender_data", verbose=verbose)
        call_command("load_report_reader_flag_data", verbose=verbose)
        call_command("load_pdf_type_data", verbose=verbose)
        call_command("load_unit_data", verbose=verbose)
        call_command("load_disease_data", verbose=verbose)
        call_command("load_event_data", verbose=verbose)
        call_command("load_organ_data", verbose=verbose)
        call_command("load_contraindication_data", verbose=verbose)
        call_command("load_finding_data", verbose=verbose)
        call_command("load_examination_indication_data", verbose=verbose)
        call_command("load_examination_data", verbose=verbose)
        call_command("load_lab_value_data", verbose=verbose)
        call_command("load_medication_data", verbose=verbose)

        # Load AI Model Data
        call_command("load_ai_model_label_data", verbose=verbose)
        call_command("load_ai_model_data", verbose=verbose)
