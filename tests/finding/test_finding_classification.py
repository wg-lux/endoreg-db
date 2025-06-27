from django.test import TestCase
from logging import getLogger


import logging

from endoreg_db.models.medical.finding.finding_classification import FindingClassification

from ..helpers.data_loader import (
    load_data
)
from endoreg_db.models import Examination

BOWEL_PREP_FINDING_NAME = "bowel_preparation_simplified"
BBPS_SIMPLE_CLASSIFICATION_NAME = "bowel_prep_boston_simplified"

logger = getLogger(__name__)
logger.setLevel(logging.INFO) # Changed to INFO for more verbose logging during test development


class ColonoscopyFindingTest(TestCase):
    
    # @classmethod
    # def setUpTestData(cls):
    def setUp(self):
        """
        Initializes test data for the CommonFindingTest class.
        
        Creates and retrieves Finding instances.
        """
        load_data()
        
        self.colonoscopy = Examination.objects.get(name="colonoscopy")

        self.colo_examination_indications = self.colonoscopy.indications.all()
        
        # cls.colonoscopy.findings is a ManyToManyField, so we need to filter by the examination
        self.colo_finding_bp = self.colonoscopy.findings.get(name = BOWEL_PREP_FINDING_NAME)
        self.examination_colonoscopy = Examination.objects.get(name="colonoscopy")
        self.bbps_simple_classification = FindingClassification.objects.get(name=BBPS_SIMPLE_CLASSIFICATION_NAME)

    def test_colonoscopy_bbps_simple_choices(self):
        logger.info("Testing colonoscopy finding bowel_preparation_simplified")
        
        # Check if the colonoscopy examination has findings
        self.assertIsNotNone(self.colo_finding_bp, "Colonoscopy finding with name 'colo_finding_bp_simple' not found.")
        
        # Make sure that the finding is linked to the colonoscopy examination
        self.assertIn(self.colo_finding_bp, self.colonoscopy.findings.all(), "Colonoscopy should have 'bowel_preparation_simplified' finding associated with it.")
        
        choices = self.bbps_simple_classification.choices.all()

        logger.info(f"Choices for {self.bbps_simple_classification.name}: {choices}")
        self.assertTrue(choices.exists(), f"BBPS simple classification should have choices associated with it, but found none for {self.bbps_simple_classification.name}.")

   