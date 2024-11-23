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
    TEST_EXAMINATION_NAME_STRINGS
)

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_center_created(self):
        _ = Center.objects.get(name=TEST_CENTER_NAME)
        assert _

    def test_create_generic_patient(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        assert patient
    
    def test_create_generic_patient_with_examination(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        patient_examination = patient.create_examination()

        assert patient_examination


    def test_create_generic_patient_with_examinations(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        examination_name_strings = TEST_EXAMINATION_NAME_STRINGS

        for examination_name_str in examination_name_strings:

            patient_examination = patient.create_examination(
                examination_name_str=examination_name_str
            )

            assert patient_examination


    