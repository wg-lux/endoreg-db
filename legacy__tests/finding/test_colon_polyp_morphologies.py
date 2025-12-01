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


class ColonPolypMorphologyTest(TestCase):
    
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

    def test_colon_polyp_morphologies(self):
        """
        Test if the colon polyp finding is correctly associated with the colonoscopy examination.
        
        Checks if the colon polyp finding is present in the findings of the colonoscopy examination.
        """
        logger.info("Testing colon polyp finding")
        
        # Check if the colon polyp finding exists
        colon_polyp_finding = self.colo_findings.get(name="colon_polyp")
        self.assertIsNotNone(colon_polyp_finding, "Colonoscopy should have a 'colon polyp' finding associated with it.")
        
        # get the morphology classifications for the colon polyp finding
        colon_polyp_morphologies = colon_polyp_finding.get_morphology_classifications()
        
        self.assertGreater(len(colon_polyp_morphologies), 0, "Colonoscopy should have morphology classifications for 'colon polyp' finding.")
        
        # assert "colon_lesion_paris" and "colon_lesion_nice" are in the morphology classifications
        morphology_names = [morphology.name for morphology in colon_polyp_morphologies]
        self.assertIn("colon_lesion_paris", morphology_names, "Colonoscopy should have 'colon_lesion_paris' morphology classification for 'colon polyp' finding.")
        self.assertIn("colon_lesion_nice", morphology_names, "Colonoscopy should have 'colon_lesion_nice' morphology classification for 'colon polyp' finding.")
