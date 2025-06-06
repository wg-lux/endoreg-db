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


class ColonPolypInterventionTest(TestCase):
    
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


    def test_colon_polyp_interventions(self):
        """
        
        """
        logger.info("Testing colon polyp finding")
        
        # Check if the colon polyp finding exists
        colon_polyp_finding = self.colo_findings.get(name="colon_polyp")
        self.assertIsNotNone(colon_polyp_finding, "Colonoscopy should have a 'colon polyp' finding associated with it.")
        
        # get the polyp location classifications
        colon_polyp_locations = colon_polyp_finding.finding_interventions.all()

        self.assertTrue(colon_polyp_locations.exists(), "Colonoscopy should have colon polyp locations associated with it.")

        names = [location.name for location in colon_polyp_locations]

        expected_names = [
            "colon_lesion_polypectomy_cold_snare",
            "colon_lesion_polypectomy_hot_snare",
            "colon_lesion_injection_liftup",
            "colon_lesion_injection_vasoactive",
            "colon_lesion_biopsy",
            "colon_lesion_emr",
            "colon_lesion_esd",
            "colon_lesion_clip",
            # "colon_lesion_over_the_scope_clip",
            "colon_lesion_eftr",
        ]

        for expected_name in expected_names:
            self.assertIn(expected_name, names, f"Colonoscopy should have '{expected_name}' intervention associated with it.")
