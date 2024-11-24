from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, 
    PatientExamination, 
    PatientFinding,
    PatientFindingLocation,
    Finding,
    FindingLocationClassification,
    FindingLocationClassificationChoice
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
        call_command("load_contraindication_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_organ_data", stdout=out)
        call_command("load_examination_data", stdout=out)
        call_command("load_lab_value_data", stdout=out)
        call_command("load_finding_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_create_generic_test_patient_with_colonoscopy_findings(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        patient_examination:PatientExamination = patient.create_examination(examination_name_str="colonoscopy")

        finding_choices = patient_examination.get_finding_choices()

        for finding in finding_choices:
            patient_examination.create_finding(finding)

        patient_examination.refresh_from_db()

        # Check if findings were added

        assert len(patient_examination.get_findings()) == len(finding_choices)

        return patient


    def test_create_colonoscopy_patient_with_random_findings(self):
        FINDING_LOCATION_CLASSIFICATION_NAME = "colonoscopy_default"
        EXAMINATION_NAME = "colonoscopy"

        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        patient_examination:PatientExamination = patient.create_examination(
            examination_name_str=EXAMINATION_NAME
        )

        # fetch location classification
        location_classification = FindingLocationClassification.objects.get(
            name=FINDING_LOCATION_CLASSIFICATION_NAME
        )

        examinations = location_classification.examinations.all()
        assert patient_examination.examination in examinations

        findings = location_classification.findings.filter(
            examinations__in=[patient_examination.examination]
        )
        findings = [_ for _ in findings]

        for finding in findings:
            # print(finding)
            patient_finding = patient_examination.create_finding(finding)
            patient_finding_location = patient_finding.set_random_location(location_classification)
            _subcat_dict = patient_finding_location.set_random_subcategories()
            _num_dict = patient_finding_location.set_random_numerical_descriptors()


    def test_create_generic_patient_with_finding_and_location(self):
        FINDING_LOCATION_CLASSIFICATION_NAME = "colonoscopy_default"
        EXAMINATION_NAME = "colonoscopy"

        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        patient_examination:PatientExamination = patient.create_examination(
            examination_name_str=EXAMINATION_NAME
        )

        # fetch location classification
        location_classification = FindingLocationClassification.objects.get(
            name=FINDING_LOCATION_CLASSIFICATION_NAME
        )

        # # get available findings for this classification and for this examination, then create a finding
        findings = location_classification.findings.filter(
            examinations__in=[patient_examination.examination]
        )
        findings = [_ for _ in findings]
        assert findings
        
        location_choices = location_classification.choices.all()
        classification_findings = location_classification.findings.all()
        
        filtered_findings = [f for f in findings if f.name in [
            finding.name for finding in classification_findings
            ]
        ]

        i = 0
        for finding in filtered_findings:
            for location_choice in location_choices:
                i+=1
                patient_finding = patient_examination.create_finding(finding)
                patient_finding_location:PatientFindingLocation = patient_finding.add_location_choice(
                    location_choice,
                    location_classification
                )
                _subcat_dict = patient_finding_location.set_random_subcategories()
                _num_dict = patient_finding_location.set_random_numerical_descriptors()
                # print(patient_finding)
                # print(num_dict)

        print(f"Created {i} patient findings with locations")
        print(f"Tested Patient Findings {findings}")
        print(f"with location classification {location_classification}")
        print(f"which contains those location choices {location_choices}")
