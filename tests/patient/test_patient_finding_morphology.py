from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, 
    PatientExamination, 
    PatientFinding,
    Finding,
    FindingMorphologyClassification, FindingMorphologyClassificationChoice,
    FindingMorphologyClassificationType
)

from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from .conf import (
    TEST_CENTER_NAME,
)

class TestGeneratePatient(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_organ_data", stdout=out)
        call_command("load_contraindication_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        call_command("load_finding_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_create_generic_patient_with_polyp_and_morphology(self):
        print("test_create_generic_patient_with_polyp_and_morphology")
        EXAMINATION_NAME = "colonoscopy"
        FINDING_NAME = "colon_polyp"

        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        patient_examination:PatientExamination = patient.create_examination(
            examination_name_str=EXAMINATION_NAME
        )

        finding = Finding.objects.get(name=FINDING_NAME)

        # get all required morphology classification types
        required_morphology_classification_types = finding.required_morphology_classification_types.all()
        print(f"required_morphology_classification_types: {required_morphology_classification_types}")
        for morphology_classification_type in required_morphology_classification_types:
            # get all classifications for this type
            classifications = FindingMorphologyClassification.objects.filter(
                classification_type=morphology_classification_type
            )

            print(f"classifications: {classifications}")
            for classification in classifications:
            # get all choices for this classification
                choices = classification.choices.all()
                print(f"choices: {choices}")
                for choice in choices:
                    patient_finding = patient_examination.create_finding(
                        finding
                    )
                    morphology_choice = patient_finding.add_morphology_choice(
                        choice, classification
                    )

                    morphology_choice.set_random_numerical_descriptors()
                    print(f"morphology_choice: {morphology_choice}")

        # fetch patient from db
        patient.refresh_from_db()
        patient_examination.refresh_from_db()

        # print all findings and their morphology
        for patient_finding in patient_examination.get_findings():
            patient_finding:PatientFinding
            # print(patient_finding)
            for morphology in patient_finding.get_morphology_choices():
                # print(morphology)
                pass

                

