from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import InformationSource

class InformationSourceModelTest(TestCase):
    def setUp(self):
        # Create a InformationSource instance for testing
        self.information_source = InformationSource.objects.create(
            name="test",
            name_de="Test",
            name_en="Test",
            abbreviation="T",
            description="This is a Test Information Source"
        )
        
    def test_load_information_source_command(self):
        """Test if the load_information_source command runs without errors."""
        try:
            call_command("load_information_source")
        except Exception as e:
            self.fail(f"load_information_source command failed: {e}")

    def test_abbreviation_unique_constraint(self):
        """Test if the abbreviation field is unique."""
        with self.assertRaises(Exception):
            InformationSource.objects.create(
                name="duplicate",
                name_de="Duplicate",
                name_en="Duplicate",
                abbreviation=self.information_source.abbreviation,
                description="This is a duplicate Information Source"
            )
            