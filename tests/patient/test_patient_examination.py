from endoreg_db.models import (
    Patient, 
    Center,
    PatientExamination,
    ExaminationIndication,
    PatientExaminationIndication
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
    TEST_PATIENT_EXAMINATION_OUTPUT_PATH,
    TEST_PATIENT_EXAMINATION_INDICATION_OUTPUT_PATH
)

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        call_command("load_examination_indication_data", stdout=out)
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

    def test_examination_indication(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        examination_indications = ExaminationIndication.objects.all()

        for examination_indication in examination_indications:
            for examination_indication_choice in examination_indication.get_choices():
                patient_examination, patient_examination_indication = patient.create_examination_by_indication(
                    indication=examination_indication
                )

                assert patient_examination
                assert patient_examination_indication

                patient_examination_indication.indication_choice = examination_indication_choice
                patient_examination_indication.save()

        


        # get all examinations for patient
        patient_examinations = PatientExamination.objects.filter(patient=patient)

        with open(TEST_PATIENT_EXAMINATION_INDICATION_OUTPUT_PATH, "w") as f:
            for patient_examination in patient_examinations:
                f.write(f"{patient_examination}\n")
                
                examination_indications = patient_examination.get_indications()
                assert len(examination_indications) == 1
                examination_indication = examination_indications[0]
                f.write(f"Examination Indication: {examination_indication}\n")

                examination_indication_choices = patient_examination.get_indication_choices()
                assert len(examination_indication_choices) == 1
                examination_indication_choice = examination_indication_choices[0]

                f.write(f"Examination Indication Choice: {examination_indication_choice}\n\n")



    