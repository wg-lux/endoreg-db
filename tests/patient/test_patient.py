from endoreg_db.models import Patient, Center, PatientLabValue
from django.core.management import call_command
from django.test import TestCase
from io import StringIO

TEST_CENTER_NAME = "test_center"

LAB_VALUE_DICTS = [
    {
        "lab_value_name": "potassium",
        "value": 4.2,
    }
]

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_center_created(self):
        _ = Center.objects.get(name=TEST_CENTER_NAME)
        assert _

    def test_create_generic_patient(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        assert patient
        print("Patient:")
        print(patient)
        print(f"Age: {patient.age()}")
        print(f"Date of Birth: {patient.dob}")
        print(f"Gender: {patient.gender}")
        return patient

    def test_create_generic_patient_with_lab_sample(self):
        patient = self.test_create_generic_patient()
        patient.create_lab_sample(
            sample_type="generic",
            date=None, # defaults to dt.now() if not provided
            save=True
        )
        print("Lab Sample:")
        sample = patient.lab_samples.first()
        print(sample)

        # Add a lab value to the lab sample
        _dict = LAB_VALUE_DICTS[0]

        for _dict in LAB_VALUE_DICTS:
            lab_value = PatientLabValue.create_lab_value_by_sample(
                sample=sample,
                **_dict
            )

            lab_value.set_unit_from_default()
            lab_value.set_norm_values_from_default()
        
        print("Lab Values:")
        for value in sample.get_values():
            print(value)

        return patient