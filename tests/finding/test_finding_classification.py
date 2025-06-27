from django.test import TestCase
from logging import getLogger


import logging

from endoreg_db.models.medical.finding.finding_classification import FindingClassification, FindingClassificationChoice
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding

from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient   
)

from endoreg_db.models import Examination

BOWEL_PREP_FINDING_NAME = "bowel_preparation_simplified"
BBPS_SIMPLE_CLASSIFICATION_NAME = "bowel_prep_boston_simplified"
EXAMINATION_NAME = "colonoscopy"
CLASSIFICATION_CHOICE_NAME = "bowel_prep_boston_3"

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
        self.bbps_simple_choice = FindingClassificationChoice.objects.get(name=CLASSIFICATION_CHOICE_NAME)

        # Create a patient for the test
        self.patient = generate_patient()
        self.patient.save()

        # Create Examination for the patient
        self.patient_examination: PatientExamination = self.patient.create_examination(
            examination_name_str = EXAMINATION_NAME,
            save=True
        )

        self.patient_finding_bp: PatientFinding = self.patient_examination.create_finding(self.colo_finding_bp)


    def test_colonoscopy_bbps_simple_choices(self):
        logger.info("Testing colonoscopy finding bowel_preparation_simplified")
        
        # Check if the colonoscopy examination has findings
        self.assertIsNotNone(self.colo_finding_bp, "Colonoscopy finding with name 'colo_finding_bp_simple' not found.")
        
        # Make sure that the finding is linked to the colonoscopy examination
        self.assertIn(self.colo_finding_bp, self.colonoscopy.findings.all(), "Colonoscopy should have 'bowel_preparation_simplified' finding associated with it.")
        
        choices = self.bbps_simple_classification.choices.all()

        logger.info(f"Choices for {self.bbps_simple_classification.name}: {choices}")
        self.assertTrue(choices.exists(), f"BBPS simple classification should have choices associated with it, but found none for {self.bbps_simple_classification.name}.")

    def test_patient_finding_classification(self):
        pat_finding_bowel_prep = self.patient_finding_bp
        pat_finding_bowel_prep_classification = pat_finding_bowel_prep.add_classification(
            classification_id=self.bbps_simple_classification.id,
            classification_choice_id=self.bbps_simple_choice.id,  # Assuming 1 is a valid choice ID
            user=None
        )

        self.assertIsNotNone(pat_finding_bowel_prep_classification, "Patient finding classification should not be None.")

        

   