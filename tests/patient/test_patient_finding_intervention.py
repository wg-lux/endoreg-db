from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, PatientFinding, FindingMorphologyClassification,
    Finding,
    FindingLocationClassification,
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
        call_command("load_organ_data", stdout=out)
        call_command("load_contraindication_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        call_command("load_finding_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_colon_polyp_finding_interventions(self):
        import random
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        patient_examination = patient.create_examination(
            examination_name_str="colonoscopy"
        )

        finding = Finding.objects.get(name="colon_polyp")

        



        # get all location classifications for this finding
        location_classifications = finding.get_location_classifications()


        for location_classification in location_classifications:
            assert isinstance(location_classification, FindingLocationClassification)
            location_classification_choices = location_classification.get_choices()

            for location_classification_choice in location_classification_choices:
                # get random morphology choice
                required_morphology_classification_types = finding.required_morphology_classification_types.all()
                for morphology_classification_type in required_morphology_classification_types:
                    # get all classifications for this type
                    classification:FindingMorphologyClassification = FindingMorphologyClassification.objects.first(
                        classification_type=morphology_classification_type
                    )
                    choices = classification.get_choices()
                    choice = random.choice(choices)

                    finding_interventions = finding.finding_interventions.all()
                    for finding_intervention in finding_interventions:
                        patient_finding:PatientFinding = patient_examination.create_finding(finding)
                        
                        # Set Location
                        _patient_finding_location = patient_finding.add_location_choice(
                            location_classification_choice, location_classification
                        )

                        # Set Morphology
                        morphology_choice = patient_finding.add_morphology_choice(
                            choice, classification
                        )
                        morphology_choice.set_random_numerical_descriptors()

                        finding_intervention = patient_finding.add_intervention(finding_intervention)


                        

