from endoreg_db.models import (
    Patient, 
    Center, 
)

from .utils import create_test_patient

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_PATIENT_OUTPUT_PATH
)

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

        # Delete existing test_patient_examination_output_path
        with open(TEST_PATIENT_OUTPUT_PATH, "w") as f:
            f.write("")

    def test_create_patient(self):
        patient = create_test_patient()

        report = "Test Patient created successfully: \n"
        report += f"{patient}\n"

        # Append to test_patient_examination_output_path
        with open(TEST_PATIENT_OUTPUT_PATH, "a") as f:
            f.write(report)

        assert patient

    def test_create_100_patients(self):
        # delete all patients
        Patient.objects.all().delete()

        # Create 100 patients
        for i in range(100):
            _patient = create_test_patient()

        # calculate mean, min, max and std of patient.age
        patients = Patient.objects.all()
        ages = [patient.age() for patient in patients]

        mean_age = sum(ages) / len(ages)
        min_age = min(ages)
        max_age = max(ages)

        std_age = (sum([(age - mean_age)**2 for age in ages]) / len(ages))**0.5

        report = "100 Test Patients created successfully: \n"
        report += f"Mean age: {mean_age}\n"
        report += f"Min age: {min_age}\n"
        report += f"Max age: {max_age}\n"
        report += f"Std age: {std_age}\n"

        # Append to test_patient_examination_output_path
        with open(TEST_PATIENT_OUTPUT_PATH, "a") as f:
            f.write(report)

