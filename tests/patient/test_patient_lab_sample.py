from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, 
)

from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from .conf import (
    TEST_CENTER_NAME,
    LAB_VALUE_DICTS,
    LAB_VALUE_W_DIST_DICTS
)



class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_create_generic_patient_with_lab_sample(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        patient.create_lab_sample(
            sample_type="generic",
            date=None, # defaults to dt.now() if not provided
            save=True
        )
        # print("Lab Sample:")
        sample = patient.lab_samples.first()
        # print(sample)

        # Add a lab value to the lab sample
        _dict = LAB_VALUE_DICTS[0]

        for _dict in LAB_VALUE_DICTS:
            lab_value = PatientLabValue.create_lab_value_by_sample(
                sample=sample,
                **_dict
            )

            lab_value.set_unit_from_default()
            lab_value.set_norm_values_from_default()

        for _dict in LAB_VALUE_W_DIST_DICTS:
            lab_value = PatientLabValue.create_lab_value_by_sample(
                sample=sample,
                **_dict
            )
            
            lab_value.set_unit_from_default()
            lab_value.set_norm_values_from_default()
            lab_value.set_value_by_distribution()

        
        print("Lab Values:")
        for value in sample.get_values():
            # print(value)
            pass

        return patient