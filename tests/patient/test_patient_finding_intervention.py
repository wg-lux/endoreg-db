from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, PatientFinding, FindingMorphologyClassification,
    Finding, PatientFindingIntervention,
    FindingLocationClassification,
)

from .utils import create_patient_with_colonoscopy

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_EXAMINATION_NAME_STRINGS,
    TEST_PATIENT_INTERVENTION_OUTPUT_PATH,
    COLONOSCOPY_FINDING_LOCATION_CLASSIFICATION_NAME
)
import random

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
        patient, patient_examination = create_patient_with_colonoscopy(center_name=TEST_CENTER_NAME)

        finding = Finding.objects.get(name="colon_polyp")

        # get all location classifications for this finding
        location_classification = FindingLocationClassification.objects.get(
            name=COLONOSCOPY_FINDING_LOCATION_CLASSIFICATION_NAME
        )

        location_classification_choices = location_classification.get_choices()
        required_morphology_classification_types = finding.get_required_morphology_classification_types()
        # Select random
        location_classification_choice = random.choice(location_classification_choices)
        morphology_classification_type = random.choice(required_morphology_classification_types)
        
        morphology_classification:FindingMorphologyClassification = FindingMorphologyClassification.objects.filter(
            classification_type=morphology_classification_type
        ).first()
        
        i = 0


        morphology_classification_choices = morphology_classification.get_choices()
        morphology_classification_choice = random.choice(morphology_classification_choices)

        finding_interventions = finding.finding_interventions.all()
        for finding_intervention in finding_interventions:
            i+=1
            patient_finding:PatientFinding = patient_examination.create_finding(finding)
            
            # Set Location
            _patient_finding_location = patient_finding.add_location_choice(
                location_classification_choice, location_classification
            )

            # Set Morphology
            patient_finding_morphology = patient_finding.add_morphology_choice(
                morphology_classification_choice, morphology_classification
            )
            patient_finding_morphology.set_random_numerical_descriptors()

            _patient_finding_intervention = patient_finding.add_intervention(
                finding_intervention
            )

        patient_examination.refresh_from_db()
        patient_findings = patient_examination.get_findings()
        assert patient_findings
        out = f"Generated {i} interventions for {len(patient_findings)} findings\n"
        for patient_finding in patient_findings:
            out += f"{patient_finding}\n"
            for intervention in patient_finding.get_interventions():
                out += f"Finding Location: {patient_finding.get_locations()}\n"
                out += f"Finding Morphology: {patient_finding.get_morphologies()}\n"
                out += f"\t{intervention}\n"

            out += "\n\n"

        with open(TEST_PATIENT_INTERVENTION_OUTPUT_PATH, "w") as f:
            f.write(out)

