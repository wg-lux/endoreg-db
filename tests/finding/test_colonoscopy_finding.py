from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    LabValue,
)
from endoreg_db.models.medical.laboratory.lab_value import CommonLabValues # Keep for class structure
import logging

from ..helpers.data_loader import (
    load_data
)
from endoreg_db.models import Finding, Examination, Patient, Gender
# from endoreg_db.models.other.distribution import NumericValueDistribution # For type hinting/mocking
from unittest.mock import MagicMock, patch, PropertyMock


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
        self.colo_findings = self.colonoscopy.findings.all()

    # def setUp(self):
    #     """
    #     """
    #     pass

    def test_colonoscopy_findings(self):
        """
        Test if the colonoscopy findings are correctly associated with the colonoscopy examination.
        
        Checks if the findings related to colonoscopy are correctly linked to the colonoscopy examination.
        """
        logger.info("Testing colonoscopy findings")
        
        # Check if the colonoscopy examination has findings
        self.assertTrue(self.colo_findings.exists(), "Colonoscopy should have findings associated with it.")
        
        
    def test_colon_polpy_finding(self):
        """
        Test if the colon polyp finding is correctly associated with the colonoscopy examination.
        
        Checks if the colon polyp finding is present in the findings of the colonoscopy examination.
        """
        logger.info("Testing colon polyp finding")
        
        # Check if the colon polyp finding exists
        colon_polyp_finding = self.colo_findings.get(name="colon_polyp")
        self.assertIsNotNone(colon_polyp_finding, "Colonoscopy should have a 'colon polyp' finding associated with it.")
        
        # Check if the colon polyp finding is linked to the colonoscopy examination
        self.assertIn(colon_polyp_finding, self.colonoscopy.findings.all(), "Colonoscopy should have 'colon polyp' finding associated with it.")

