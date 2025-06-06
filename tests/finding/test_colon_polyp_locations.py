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


class ColonPolypLocationTest(TestCase):
    
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


    def test_colon_polyp_locations(self):
        """
        
        """
        logger.info("Testing colon polyp finding")
        
        # Check if the colon polyp finding exists
        colon_polyp_finding = self.colo_findings.get(name="colon_polyp")
        self.assertIsNotNone(colon_polyp_finding, "Colonoscopy should have a 'colon polyp' finding associated with it.")
        
        # get the polyp location classifications
        colon_polyp_locations = colon_polyp_finding.location_classifications.all()

        self.assertTrue(colon_polyp_locations.exists(), "Colonoscopy should have colon polyp locations associated with it.")

        # make sure colonoscopy_coarse and colonoscopy_default are in the locations
        names = [location.name for location in colon_polyp_locations]
        self.assertIn("colonoscopy_coarse", names, "Colonoscopy should have 'colonoscopy_coarse' location associated with it.")
        self.assertIn("colonoscopy_default", names, "Colonoscopy should have 'colonoscopy_default' location associated with it.")

