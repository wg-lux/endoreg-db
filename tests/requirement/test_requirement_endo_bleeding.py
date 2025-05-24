
from math import e
from django.test import TestCase
from logging import getLogger
import shutil

from endoreg_db.models import (
    Requirement, 
    RequirementOperator,
    RequirementType,
    FindingIntervention,
    ExaminationIndication,
    PatientExamination
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
        from endoreg_db.models import (
            PatientExamination
        )
        
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
        # Create sample patient examination 
        examination_indication = ExaminationIndication.objects.get(
            name = "colonoscopy_lesion_removal_large"
        )

        requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        self.assertIsInstance(requirement, Requirement)
        requirement_links = requirement.links
        self.assertIsInstance(requirement_links, RequirementLinks)

        examination_indication_links = examination_indication.links
        self.assertIsInstance(examination_indication_links, RequirementLinks)
        examination_indication_finding_interventions = examination_indication_links.finding_interventions
        self.assertGreater(len(examination_indication_finding_interventions), 0)

        # print(f"Examination indication links: {examination_indication_links.active()}")
        # print(f"Requirement links: {requirement_links.active()}")

        match_any_true = requirement_links.match_any(other = examination_indication_links)
        self.assertTrue(match_any_true, "Requirement links should match examination indication links")
        
        # finding_interventions = patient_examination_indication.examination_indication.expected_interventions.all()
        # # make sure we have at least one intervention
        # self.assertGreater(len(finding_interventions), 0)
        
        # requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        # self.assertIsInstance(requirement, Requirement)

        # # assert sample_indication in requirement.finding_interventions
        # self.assertIn(
        #     sample_intervention, 
        #     requirement.finding_interventions.all()
        # )
        
        # # assert "patient_examination" in requirement.requirement_types
        # self.assertIn(
        #     self.requirement_type_patient_examination, 
        #     requirement.requirement_types.all()
        # )

        # # assert operator_models_match_any in requirement.operators
        # self.assertIn(
        #     self.operator_models_match_any, 
        #     requirement.operators.all()
        # )

        # operator_type_tuples = requirement.operator_type_tuples
        # from endoreg_db.models.requirement.requirement_evaluation import (
        #     OperatorTypeTuple
        # )
        # self.assertIsInstance(operator_type_tuples, list)
        # self.assertIsInstance(operator_type_tuples[0], OperatorTypeTuple)
        # self.assertEqual(
        #     operator_type_tuples[0].operator, 
        #     self.operator_models_match_any
        # )
