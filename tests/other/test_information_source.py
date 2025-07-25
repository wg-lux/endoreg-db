from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import InformationSource, InformationSourceType
from ..helpers.data_loader import load_information_source_data

class InformationSourceModelTest(TestCase):
    def setUp(self):
        load_information_source_data()
        self.information_source_type = InformationSourceType.objects.create(
            name="test_type",
            description="This is a Test Information Source Type"
        )
        # Create a InformationSource instance for testing
        self.information_source = InformationSource.objects.create(
            name="test",
            abbreviation="T",
            description="This is a Test Information Source",
        )
        self.information_source.information_source_types.add(self.information_source_type)
        
    def test_load_information_source_command(self):
        """Test if the load_information_source command runs without errors."""
        try:
            call_command("load_information_source")
        except Exception as e:
            self.fail(f"load_information_source command failed: {e}")

    def test_abbreviation_unique_constraint(self):
        """Test if the abbreviation field is unique."""
        from django.db.utils import IntegrityError
        with self.assertRaises(IntegrityError):
            InformationSource.objects.create(
                name="duplicate",
                abbreviation=self.information_source.abbreviation,
                description="This is a duplicate Information Source"
            )
            