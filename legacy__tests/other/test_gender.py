from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import Gender

required_gender_names = [
    "male",
    "mostly_male",
    "female",
    "mostly_female",
    "other",
    "unknown",
]

class GenderModelTest(TestCase):
    def setUp(self):
        # Create a Unit instance for testing
        self.gender = Gender.objects.create(
            name = "test",  
            abbreviation = "T",
            description = "This is a Test Gender"
        )
        
    def test_load_gender_data_command(self):
        """Test if the load_gender_data command runs without errors."""
        try:
            call_command("load_gender_data")
        except Exception as e:
            self.fail(f"load_gender_data command failed: {e}")

        for name in required_gender_names:
            g = Gender.objects.get(name=name)
            self.assertIsNotNone(g, "Gender with name '{}' not found".format(name))

    def test_gender_creation(self):
        """Test if the unit is created correctly."""
        self.assertEqual(self.gender.name, "test")
        self.assertEqual(self.gender.abbreviation, "T")
        self.assertEqual(self.gender.description, "This is a Test Gender")
