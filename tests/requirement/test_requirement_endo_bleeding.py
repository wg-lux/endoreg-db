
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
import logging
from django.conf import settings


from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    get_default_center, 
    get_gender_m_or_f,
    generate_patient
)

from ..test_settings import (
    ASSET_DIR,
    RUN_VIDEO_TESTS
)

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger(__name__)
logger.setLevel(logging.WARNING)

# Check for ffmpeg executable once
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning("ffmpeg command not found. Frame extraction tests will be skipped.")


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


    def test_high_bleed_risk_examination(self):
        # Create sample patient examination 
        sample_indication = ExaminationIndication.objects.get(
            name = "colonoscopy_lesion_removal_large"
        )
        sample_intervention = sample_indication.expected_interventions.get(name ="colon_lesion_esd")

        patient_examination, patient_examination_indication = self.patient.create_examination_by_indication(
            indication = sample_indication
        )
        self.assertIsInstance(patient_examination, PatientExamination)

        finding_interventions = patient_examination_indication.examination_indication.expected_interventions.all()
        # make sure we have at least one intervention
        self.assertGreater(len(finding_interventions), 0)
        
        requirement = Requirement.objects.get(name=self.req_name_bleeding_high)
        self.assertIsInstance(requirement, Requirement)

        # assert sample_indication in requirement.finding_interventions
        self.assertIn(
            sample_intervention, 
            requirement.finding_interventions.all()
        )
        
        # assert "patient_examination" in requirement.requirement_types
        self.assertIn(
            self.requirement_type_patient_examination, 
            requirement.requirement_types.all()
        )

        # assert operator_models_match_any in requirement.operators
        self.assertIn(
            self.operator_models_match_any, 
            requirement.operators.all()
        )

        operator_type_tuples = requirement.operator_type_tuples
        from endoreg_db.models.requirement.requirement_evaluation import (
            OperatorTypeTuple
        )
        self.assertIsInstance(operator_type_tuples, list)
        self.assertIsInstance(operator_type_tuples[0], OperatorTypeTuple)
        self.assertEqual(
            operator_type_tuples[0].operator, 
            self.operator_models_match_any
        )
