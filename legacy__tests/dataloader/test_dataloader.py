from django.test import TestCase
from ..helpers.data_loader import (
    load_ai_model_data,
    load_center_data,
    load_disease_data,
    load_endoscope_data,
    load_examination_data,
    load_examination_indication_data,
    load_finding_data,
    load_ai_model_label_data,
    load_medication_data,
    load_organ_data,
    load_contraindication_data,
    load_distribution_data,
    load_event_data,
    load_gender_data,
    load_lab_value_data,
    load_pdf_type_data,
    load_report_reader_flag_data,
    load_requirement_data,
    load_information_source_data,
    load_unit_data,
    load_risk_data,
    load_base_db_data,
)

class TestDataLoader(TestCase):
    """
    Test the data loading commands.
    Each test method runs in its own transaction, ensuring isolation.
    The database state is automatically reset after each test by Django's TestCase.
    """
    def test_load_base_db_data(self):
        """
        Test if the load_base_db_data command runs without errors.
        This command loads all base data into the database.
        """
        try:
            load_base_db_data()
        except Exception as e:
            self.fail(f"load_base_db_data command failed: {e}")

    def test_load_information_source_data(self):
        """
        Test if the load_information_source command runs without errors.
        """
        try:
            load_information_source_data()
        except Exception as e:
            self.fail(f"load_information_source command failed: {e}")

    def test_load_risk_data(self):
        """
        Test if the load_risk_data command runs without errors.
        """
        try:
            load_risk_data()
        except Exception as e:
            self.fail(f"load_risk_data command failed: {e}")

    def test_load_center_data(self):
        """
        Test if the load_center_data command runs without errors.
        """
        try:
            load_center_data()
        except Exception as e:
            self.fail(f"load_center_data command failed: {e}")

    def test_load_endoscope_data(self):
        """
        Test if the load_endoscope_data command runs without errors.
        """
        try:
            load_endoscope_data()
        except Exception as e:
            self.fail(f"load_endoscope_data command failed: {e}")

    def test_load_distribution_data(self):
        """
        Test if the load_distribution_data command runs without errors.
        """
        try:
            load_distribution_data()
        except Exception as e:
            self.fail(f"load_distribution_data command failed: {e}")

    def test_load_gender_data(self):
        """
        Test if the load load_gender_data command runs without errors.
        """
        try:
            load_gender_data()
        except Exception as e:
            self.fail(f"load_gender_data command failed: {e}")

    def test_load_report_reader_flag_data(self):
        """
        Test if the load_report_reader_flag_data command runs without errors.
        """
        try:
            load_report_reader_flag_data()
        except Exception as e:
            self.fail(f"load_report_reader_flag_data command failed: {e}")

    def test_load_pdf_type_data(self):
        """
        Test if the load_pdf_type_data command runs without errors.
        Dependencies are loaded within the test's transaction.
        """
        try:
            # Load necessary dependencies first
            load_report_reader_flag_data()
            load_endoscope_data()
            # Load the target data
            load_pdf_type_data()
        except Exception as e:
            self.fail(f"load_pdf_type_data command failed: {e}")

    def test_load_unit_data(self):
        """
        Test if the load_unit_data command runs without errors.
        """
        try:
            load_unit_data()
        except Exception as e:
            self.fail(f"load_unit_data command failed: {e}")

    def test_load_disease_data(self):
        """
        Test if the load_disease_data command runs without errors.
        """
        try:
            load_disease_data()
        except Exception as e:
            self.fail(f"load_disease_data command failed: {e}")

    def test_load_event_data(self):
        """
        Test if the load_event_data command runs without errors.
        """
        try:
            load_event_data()
        except Exception as e:
            self.fail(f"load_event_data command failed: {e}")

    def test_load_organ_data(self):
        """
        Test if the load_organ_data command runs without errors.
        """
        try:
            load_organ_data()
        except Exception as e:
            self.fail(f"load_organ_data command failed: {e}")

    def test_load_contraindication_data(self):
        """
        Test if the load_contraindication_data command runs without errors.
        """
        try:
            load_contraindication_data()
        except Exception as e:
            self.fail(f"load_contraindication_data command failed: {e}")

    def test_load_examination_data(self):
        """
        Test if the load_examination_data command runs without errors.
        """
        try:
            load_examination_data()
        except Exception as e:
            self.fail(f"load_examination_data command failed: {e}")

    def test_load_lab_value_data(self):
        """
        Test if the load_lab_value_data command runs without errors.
        """
        try:
            load_lab_value_data()
        except Exception as e:
            self.fail(f"load_lab_value_data command failed: {e}")

    def test_load_finding_data(self):
        """
        Test if the load_finding_data command runs without errors.
        """
        try:
            load_finding_data()
        except Exception as e:
            self.fail(f"load_finding_data command failed: {e}")

    def test_load_examination_indication_data(self):
        """
        Test if the load_examination_indication_data command runs without errors.
        Dependencies are loaded within the test's transaction.
        """
        try:
            # Load necessary dependencies first
            load_examination_data()
            # Load the target data
            load_examination_indication_data()
        except Exception as e:
            self.fail(f"load_examination_indication_data command failed: {e}")

    def test_load_medication_data(self):
        """
        Test if the load_medication_data command runs without errors.
        Dependencies are loaded within the test's transaction.
        """
        try:
            # Load necessary dependencies first
            load_unit_data()
            load_information_source_data()
            load_disease_data()
            load_event_data()
            # Load the target data
            load_medication_data()
        except Exception as e:
            self.fail(f"load_medication_data command failed: {e}")

    def test_load_requirement_data(self):
        """
        Test if the load_requirement_data command runs without errors.
        """
        try:
            load_requirement_data()
        except Exception as e:
            self.fail(f"load_requirement_data command failed: {e}")

    def test_load_ai_model_label_data(self):
        """
        Test if the load_ai_model_label_data command runs without errors.
        """
        try:
            load_ai_model_label_data()
        except Exception as e:
            self.fail(f"load_ai_model_label_data command failed: {e}")

    def test_load_ai_model_data(self):
        """
        Test if the load_ai_model_data command runs without errors.
        """
        try:
            load_ai_model_data()
        except Exception as e:
            self.fail(f"load_ai_model_data command failed: {e}")


