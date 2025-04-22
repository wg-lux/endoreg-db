from django.test import TestCase
from endoreg_db.models import (
    Examiner,
    Center,
    FirstName,
    LastName,
)

from ...helpers.data_loader import (
    load_center_data,
)

from ...helpers.default_objects import (
    get_default_center,
)

class ExaminerModelTest(TestCase):
    def setUp(self):
        load_center_data()
        self.center = get_default_center()
        
        # Create a FirstName and LastName instance for testing
        self.first_name = FirstName.objects.create(name="John")
        self.last_name = LastName.objects.create(name="Doe")
        
        # Create an Examiner instance for testing
        self.examiner, self.examiner_created = Examiner.custom_get_or_create(
            first_name=self.first_name,
            last_name=self.last_name,
            center=self.center,
            substitute_names=False,
        )

    def test_examiner_creation(self):
        """Test if the examiner is created correctly."""
        self.assertIsInstance(self.examiner, Examiner)
        self.assertEqual(self.examiner.first_name, self.first_name.name)
        self.assertEqual(self.examiner.last_name, self.last_name.name)
        self.assertEqual(self.examiner.center, self.center)
        self.assertTrue(self.examiner_created)
