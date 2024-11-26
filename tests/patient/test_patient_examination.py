from endoreg_db.models import (
    Patient, 
    Center,
    PatientExamination,
)
from .utils import (
    create_patient_with_colonoscopy,
    create_patient_with_endoscopies
)

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_EXAMINATION_NAME_STRINGS,
    TEST_PATIENT_EXAMINATION_OUTPUT_PATH
)

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

        # delete existing test_patient_examination_output_path
        with open(TEST_PATIENT_EXAMINATION_OUTPUT_PATH, "w") as f:
            f.write("")
    
    def test_create_colonoscopy(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        patient_examination = patient.create_examination()

        assert patient_examination


    def test_create_endoscopies(self):
        # delete all patients and patient examinations
        Patient.objects.all().delete()
        PatientExamination.objects.all().delete()

        patient, patient_examinations = create_patient_with_endoscopies(center_name=TEST_CENTER_NAME)

        examination_name_strings = TEST_EXAMINATION_NAME_STRINGS
        out_str = ""

        for examination_name_str in examination_name_strings:
            patient_examination = PatientExamination.objects.get(
                patient=patient,
                examination__name=examination_name_str
            )

            assert patient_examination

            out_str += f"{patient_examination}\n"
            out_str += f"Age at examination: {patient_examination.get_patient_age_at_examination()}\n\n"

        with open(TEST_PATIENT_EXAMINATION_OUTPUT_PATH, "w") as f:
            f.write(out_str)


    