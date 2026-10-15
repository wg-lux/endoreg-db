from django.test import TestCase
from django.core.management import call_command
from endoreg_db.models import Center, FirstName, LastName


class CenterModelTest(TestCase):
    def setUp(self):
        # Create a Center instance for testing
        self.center = Center.objects.create(
            name="test_center",
        )

    def test_load_center_data_command(self):
        """Test if the load_center command runs without errors."""
        try:
            call_command("load_center_data")
        except Exception as e:
            self.fail(f"load_center_data command failed: {e}")

        # get Würzburg
        center: Center = Center.objects.get(name="university_hospital_wuerzburg")
        fn = [str(_.name).lower() for _ in center.first_names.all()]
        ln = [str(_.name).lower() for _ in center.last_names.all()]
        self.assertEqual(fn, [])
        self.assertEqual(ln, [])

    def test_reload_preserves_locally_imported_names(self):
        call_command("load_center_data")
        center = Center.objects.get(name="university_hospital_wuerzburg")
        first = FirstName.objects.create(name="Local")
        last = LastName.objects.create(name="Employee")
        center.first_names.add(first)
        center.last_names.add(last)
        call_command("load_center_data")
        self.assertEqual(list(center.first_names.all()), [first])
        self.assertEqual(list(center.last_names.all()), [last])
