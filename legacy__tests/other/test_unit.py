from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import Unit

class UnitModelTest(TestCase):
    def setUp(self):
        # Create a Unit instance for testing
        self.unit = Unit.objects.create(
            name="Test Unit",
            abbreviation="TU",
            description="This is a test unit."
        )
        
    def test_load_unit_data_command(self):
        """Test if the load_unit_data command runs without errors."""
        try:
            call_command("load_unit_data")
        except Exception as e:
            self.fail(f"load_unit_data command failed: {e}")

    def test_unit_creation(self):
        """Test if the unit is created correctly."""
        self.assertEqual(self.unit.name, "Test Unit")
        self.assertEqual(self.unit.abbreviation, "TU")
        self.assertEqual(self.unit.description, "This is a test unit.")
