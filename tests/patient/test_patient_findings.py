from endoreg_db.models import (
    Patient, 
    Center, 
    PatientLabValue, 
    PatientExamination, 
    PatientFinding,
    PatientFindingLocation,
    Finding,
    FindingLocationClassification,
    FindingLocationClassificationChoice,
    FindingMorphologyClassification,
    FindingMorphologyClassificationChoice,
    FindingMorphologyClassificationType,
)
from typing import List
from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from .conf import (
    TEST_CENTER_NAME,
    COLONOSCOPY_FINDING_LOCATION_CLASSIFICATION_NAME,
    TEST_PATIENT_FINDINGS_OUTPUT_PATH,
    TEST_PATIENT_FINDING_LOCATIONS_OUTPUT_PATH,
    TEST_PATIENT_FINDING_MORPHOLOGIES_OUTPUT_PATH,
    TEST_POLYP_MORPHOLOGY_CLASSIFICATION_NAMES
)

from .utils import (
    create_patient_with_colonoscopy,
    create_test_patient
)

class TestGeneratePatientFindings(TestCase):
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
        patient, patient_examination = create_patient_with_colonoscopy(center_name=TEST_CENTER_NAME)
        available_findings = patient_examination.get_available_findings()

        for finding in available_findings:
            patient_examination.create_finding(finding)

        patient_examination.refresh_from_db()
        assert len(patient_examination.get_findings()) == len(available_findings)

        return patient


    def test_create_colonoscopy_patient_findings_locations(self):
        patient, patient_examination = create_patient_with_colonoscopy(center_name=TEST_CENTER_NAME)
        
        # fetch location classification
        location_classification = FindingLocationClassification.objects.get(
            name=COLONOSCOPY_FINDING_LOCATION_CLASSIFICATION_NAME
        )

        available_examination_findings = patient_examination.get_available_findings()
        filtered_findings = location_classification.remove_unavailable_findings(
            available_examination_findings
        )

        location_choices = location_classification.get_choices()
        i = 0
        for finding in filtered_findings:
            for location_choice in location_choices:
                location_choice:FindingLocationClassificationChoice
                subcategories = location_choice.get_subcategories()
                numerical_descriptors = location_choice.get_numerical_descriptors()

                for subcategory, subcategory_dict in subcategories.items():
                    required:bool = subcategory_dict.get("required", False)
                    if required:    
                        subcategory_choices = subcategory_dict.get("choices", [])
                        if subcategory_choices:
                            for choice in subcategory_choices:
                                i+=1
                                patient_finding = patient_examination.create_finding(finding)
                                
                                patient_finding_location = patient_finding.add_location_choice(
                                    location_choice, location_classification
                                )

                                patient_finding_location.set_subcategory(
                                    subcategory,
                                    choice
                                )
                        else: 
                            i+=1
                            patient_finding = patient_examination.create_finding(finding)
                            patient_finding_location = patient_finding.add_location_choice(
                                location_choice, location_classification
                            )

                for numerical_descriptor, numerical_descriptor_dict in numerical_descriptors.items():
                    required:bool = numerical_descriptor_dict.get("required", False)
                    if required:
                        i+=1
                        patient_finding = patient_examination.create_finding(finding)
                        patient_finding_location = patient_finding.add_location_choice(
                            location_choice, location_classification
                        )

                        patient_finding_location.set_random_numerical_descriptor(numerical_descriptor)
                            
        out = ""
        out += f"Created {i} patient findings with locations\n"
        out += f"Tested Patient Findings {filtered_findings}\n"
        out += f"with location classification {location_classification}\n"
        out += f"which contains those location choices {location_choices}\n"
        out += "\n------------\n\n"

        generated_findings = patient_examination.get_findings()

        for patient_finding in generated_findings:
            out += f"Patient Finding: {patient_finding}\n"
            patient_finding_locations = patient_finding.get_locations()
            for _patient_finding_location in patient_finding_locations:
                out += f"Patient Finding Location: {_patient_finding_location}\n"
                out += f"Subcategories: {_patient_finding_location.subcategories}\n"
                out += f"Numerical Descriptors: {_patient_finding_location.numerical_descriptors}\n"
                out += "\n\n"

        with open(TEST_PATIENT_FINDING_LOCATIONS_OUTPUT_PATH, "w") as f:
            f.write(out)


    def test_create_colonoscopy_polyp_morphologies(self):
        FINDING_NAME = "colon_polyp"
        out = ""        

        patient, patient_examination = create_patient_with_colonoscopy(center_name=TEST_CENTER_NAME)

        # get classification types required by colon_polyp
        finding_polyp:Finding = Finding.objects.get(name=FINDING_NAME)

        assert finding_polyp

        req_morphology_classification_types = finding_polyp.get_required_morphology_classification_types()

        morphology_classifications = []
        _types = []
        for classification_name in TEST_POLYP_MORPHOLOGY_CLASSIFICATION_NAMES:
            # print(f"fetching {classification_name}")
            classification = FindingMorphologyClassification.objects.get(
                name=classification_name
            )
            morphology_classifications.append(classification)
            _types.append(classification.classification_type)

        for _ in req_morphology_classification_types:
            assert _ in _types

        i = 0

        morphology_classifications:List[FindingMorphologyClassification]
        for morphology_classification in morphology_classifications:
            morphology_choices = morphology_classification.get_choices()
            for morphology_choice in morphology_choices:
                subcategories= morphology_choice.subcategories
                numerical_descriptors = morphology_choice.numerical_descriptors

                for subcategory, subcategory_dict in subcategories.items():
                    required:bool = subcategory_dict.get("required", False)
                    if required:
                        subcategory_choices = subcategory_dict.get("choices", [])
                        if subcategory_choices:
                            for choice in subcategory_choices:
                                i+=1
                                patient_finding = patient_examination.create_finding(finding_polyp)
                                patient_finding_morphology = patient_finding.add_morphology_choice(
                                    morphology_choice, morphology_classification
                                )
                                patient_finding_morphology.set_subcategory(
                                    subcategory,
                                    choice
                                )
                        else:
                            i+=1
                            patient_finding = patient_examination.create_finding(finding_polyp)
                            patient_finding_morphology = patient_finding.add_morphology_choice(
                                morphology_choice, morphology_classification
                            )

                for numerical_descriptor, numerical_descriptor_dict in numerical_descriptors.items():
                    required:bool = numerical_descriptor_dict.get("required", False)
                    if required:
                        i+=1
                        patient_finding = patient_examination.create_finding(finding_polyp)
                        patient_finding_morphology = patient_finding.add_morphology_choice(
                            morphology_choice, morphology_classification
                        )
                        patient_finding_morphology.set_numerical_descriptor_random(
                            numerical_descriptor
                        )


        out += f"Created {i} patient findings with morphologies\n"
        out += f"Tested Patient Findings for {FINDING_NAME}\n\n----\n\n"

        generated_findings = patient_examination.get_findings()

        for patient_finding in generated_findings:
            out += f"Patient Finding: {patient_finding}\n"
            patient_finding_morphologies = patient_finding.get_morphologies()
            for _patient_finding_morphology in patient_finding_morphologies:
                out += f"Patient Finding Morphology: {_patient_finding_morphology}\n"
                out += f"Subcategories: {_patient_finding_morphology.subcategories}\n"
                out += f"Numerical Descriptors: {_patient_finding_morphology.numerical_descriptors}\n"
                out += "\n\n"
            
        with open(TEST_PATIENT_FINDING_MORPHOLOGIES_OUTPUT_PATH, "w") as f:
            f.write(out)
