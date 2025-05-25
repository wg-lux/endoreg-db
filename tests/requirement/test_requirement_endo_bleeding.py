from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    Requirement, 
    RequirementOperator,
    RequirementType,
    FindingIntervention,
    ExaminationIndication,
    PatientExamination,
    PatientExaminationIndication # Added import
)

from endoreg_db.utils.links.requirement_link import RequirementLinks
        
import logging

from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient
)



logger = getLogger(__name__)
logger.setLevel(logging.WARNING)


class RequirementTest(TestCase):
    def setUp(self):  
        load_data()

        self.req_name_bleeding_high = "endoscopy_intervention_bleeding_risk_high"
        self.req_name_bleeding_low = "endoscopy_intervention_bleeding_risk_low"



        self.patient = generate_patient()
        self.patient.save()

        self.operator_models_match_any = RequirementOperator.objects.get(name="models_match_any")
        self.assertIsInstance(self.operator_models_match_any, RequirementOperator)

        self.requirement_type_patient_examination = RequirementType.objects.get(name="patient_examination")
        self.assertIsInstance(self.requirement_type_patient_examination, RequirementType)

    def test_requirement_creation(self):
        requirement = Requirement.objects.first()
        self.assertIsInstance(requirement, Requirement)
        logger.info(f"First Requirement in db: {requirement.name}") 

    def test_requirement_data_model_dict(self):
        
        requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        self.assertIsInstance(requirement, Requirement)

        data_model_dict = requirement.data_model_dict

        self.assertIsInstance(data_model_dict, dict)

        self.assertIn("patient_examination", data_model_dict)
        self.assertEqual(
            data_model_dict["patient_examination"],
            PatientExamination
        )


    def test_high_bleed_risk_endo_intervention(self):
        """
        Test if the requirement for high bleeding risk endoscopy intervention is created correctly.
        """
        requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        self.assertIsInstance(requirement, Requirement)
        logger.info(f"Requirement for high bleeding risk endoscopy intervention: {requirement.name}") 
        self.assertEqual(requirement.name, self.req_name_bleeding_high)

        finding_interventions = requirement.finding_interventions.all()

        intervention_names = [_.name for _ in finding_interventions]
        expected_intervention_names = [
            "colon_lesion_polypectomy_hot_snare",
            "ercp_sphincterotomy",
            "colon_lesion_eftr",
        ]

        sample_intervention = finding_interventions.first()
        self.assertIsInstance(sample_intervention, FindingIntervention)

        for expected_name in expected_intervention_names:
            self.assertIn(
                expected_name, 
                intervention_names,
                f"Expected intervention '{expected_name}' not found in {intervention_names}"
            )

    def test_high_bleed_risk_examination(self):
        # Get the ExaminationIndication that implies high bleeding risk
        examination_indication_large_lesion = ExaminationIndication.objects.get(
            name = "colonoscopy_lesion_removal_large"
        )
        self.assertIsInstance(examination_indication_large_lesion, ExaminationIndication)
        self.assertTrue(hasattr(examination_indication_large_lesion, 'links'), "ExaminationIndication should have a 'links' property")
        self.assertIsInstance(examination_indication_large_lesion.links, RequirementLinks)

        # Get the Requirement
        requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        self.assertIsInstance(requirement, Requirement)

        # Assert that this requirement expects "examination_indication" as input type
        # This is a crucial check for Option C to be valid.
        # If this fails, the data fixtures for the requirement need to be updated.
        self.assertIn(ExaminationIndication, requirement.expected_models, 
                        f"Requirement {self.req_name_bleeding_high} should expect ExaminationIndication as an input model type.")

        # Evaluate the requirement against the ExaminationIndication directly
        is_fulfilled = requirement.evaluate(examination_indication_large_lesion, mode="loose")
        self.assertTrue(is_fulfilled, 
                        f"Requirement '{self.req_name_bleeding_high}' should be fulfilled by ExaminationIndication '{examination_indication_large_lesion.name}'. "
                        f"Requirement links: {requirement.links.active()}, Indication links: {examination_indication_large_lesion.links.active()}")

        # Negative test case: an indication that should not fulfill the high bleeding risk requirement
        indication_gastroscopy_baseline = ExaminationIndication.objects.get(
            name="gastroscopy_baseline"
        )
        self.assertIsInstance(indication_gastroscopy_baseline, ExaminationIndication)
        self.assertTrue(hasattr(indication_gastroscopy_baseline, 'links'), "Screening Indication should have a 'links' property")

        is_fulfilled_screening = requirement.evaluate(indication_gastroscopy_baseline, mode="loose")
        self.assertFalse(is_fulfilled_screening, 
                         f"Requirement '{self.req_name_bleeding_high}' should NOT be fulfilled by ExaminationIndication '{indication_gastroscopy_baseline.name}'. "
                         f"Requirement links: {requirement.links.active()}, Indication links: {indication_gastroscopy_baseline.links.active()}")


