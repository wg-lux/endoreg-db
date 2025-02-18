from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, LabValue
)

from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from .conf import (
    TEST_CENTER_NAME,
    LAB_VALUE_DICTS,
    LAB_VALUE_W_DIST_DICTS,
    TEST_PATIENT_LAB_SAMPLE_OUTPUT_PATH
)



class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_distribution_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_create_generic_patient_with_lab_sample(self):
        out = ""
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        sample = patient.create_lab_sample(
            sample_type="generic",
            date=None, # defaults to dt.now() if not provided
            save=True
        )

        for _dict in LAB_VALUE_DICTS:
            lab_value = PatientLabValue.create_lab_value_by_sample(
                sample=sample,
                **_dict
            )

            lab_value.set_unit_from_default()
            lab_value.set_norm_values_from_default()

        sample_2 = patient.create_lab_sample(
            sample_type="generic",
            date=None, # defaults to dt.now() if not provided
            save=True
        )

        lab_values = LabValue.objects.all()

        for lab_value in lab_values:
            try:
                patient_lab_value = PatientLabValue.create_lab_value_by_sample(
                    sample=sample_2,
                    lab_value_name = lab_value.name,
                )
                
                patient_lab_value.set_unit_from_default()
                patient_lab_value.set_norm_values_from_default()
                patient_lab_value.set_value_by_distribution()
            except Exception as e:
                out += f"\n{lab_value.name}: {e}\n"

        for value in sample.get_values():
            out += f"{value}\n"

        for value in sample_2.get_values():
            out += f"{value}\n"

        with open(TEST_PATIENT_LAB_SAMPLE_OUTPUT_PATH, "w") as f:
            f.write(out)

        return patient
    
