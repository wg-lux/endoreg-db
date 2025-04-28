
from django.test import TestCase
from logging import getLogger
import shutil

from endoreg_db.models import (
    Requirement, 
    FindingIntervention,
)
import logging
from django.conf import settings

from ..helpers.data_loader import (
    load_data
)

RUN_VIDEO_TESTS = settings.RUN_VIDEO_TESTS
assert isinstance(RUN_VIDEO_TESTS, bool), "RUN_VIDEO_TESTS must be a boolean value"


logger = getLogger(__name__)
logger.setLevel(logging.WARNING)

# Check for ffmpeg executable once
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
if not FFMPEG_AVAILABLE:
    logger.warning("ffmpeg command not found. Frame extraction tests will be skipped.")


class RuleTest(TestCase):
    def setUp(self):  
        load_data()

        self.req_name_bleeding_high = "endoscopy_intervention_bleeding_risk_high"
        self.req_name_bleeding_low = "endoscopy_intervention_bleeding_risk_low"

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

        # Create sample patient examination 


