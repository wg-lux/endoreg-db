from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import Center

class CenterModelTest(TestCase):
    def setUp(self):
        # Create a Center instance for testing
        self.center = Center.objects.create(
            name="test_center",
            name_de="Testzentrum",
            name_en="Test Center"
        )

        
    def test_load_center_data_command(self):
        """Test if the load_center command runs without errors."""
        try:
            call_command("load_center_data")
        except Exception as e:
            self.fail(f"load_center_data command failed: {e}")

        # get Würzburg
        center:Center = Center.objects.get(name="university_hospital_wuerzburg")
        fn = [str(_.name).lower() for _ in center.first_names.all()]
        ln = [str(_.name).lower() for _ in center.last_names.all()]
        self.assertIn("thomas", fn, "Expected 'thomas' in center first names")  
        self.assertIn("lux", ln, "Expected 'lux' in center last names")