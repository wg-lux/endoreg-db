from endoreg_db.models import Center, FirstName, LastName
from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import TEST_CENTER_NAME, TEST_CENTER_OUTPUT_PATH, TEST_CENTERS_AVAILABLE

class TestCenter(TestCase):
    def setUp(self):
        # Reset the output file
        with open(TEST_CENTER_OUTPUT_PATH, "w") as f:
            f.write("Test Center\n")

        # Load required data
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_name_data", stdout=out)
        call_command("load_center_data", stdout=out)

        # Create a test center
        Center.objects.create(name=TEST_CENTER_NAME)

    def write_center_details(self, f):
        # Write details about all centers
        f.write("Checking loaded centers\n")
        for center_name in TEST_CENTERS_AVAILABLE:
            center = Center.objects.get(name=center_name)
            self.assertEqual(center.name, center_name)
            f.write(f"{center.name}: {center.name_de}; {center.name_en}:\n")
            f.write(f"First Names:{center.first_names.all()}\n")
            f.write(f"Last Names:{center.last_names.all()}\n")
            f.write("\n")

    def write_name_details(self, f):
        # Write details about all loaded names
        first_names = FirstName.objects.all()
        last_names = LastName.objects.all()

        f.write("Checking loaded names\n")
        f.write("\nFirst Names:\n")
        for first_name in first_names:
            f.write(f"{first_name}\n")

        f.write("\nLast Names:\n")
        for last_name in last_names:
            f.write(f"{last_name}\n")

    def test_center_created(self):
        # Test center creation
        center = Center.objects.get(name=TEST_CENTER_NAME)
        self.assertEqual(center.name, TEST_CENTER_NAME)

        # Append details to the output file
        with open(TEST_CENTER_OUTPUT_PATH, "a") as f:
            f.write(f"{center}\n")
            f.write(f"{center.name}\n")

    def test_centers_and_names_output(self):
        # Consolidate output into one test for better control
        with open(TEST_CENTER_OUTPUT_PATH, "a") as f:
            self.write_center_details(f)
            self.write_name_details(f)
