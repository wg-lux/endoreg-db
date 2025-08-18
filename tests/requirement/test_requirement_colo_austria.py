import logging
from django.test import TestCase
from django.utils import timezone
import datetime

from endoreg_db.models import (
    RequirementSet,
    PatientExamination,
    RequirementSetType,
    LabValue,
    PatientLabValue,
    Unit,
    Gender,
    # GenderChoice,
    # Requirement # Added for direct requirement checks if needed
)
from endoreg_db.models.medical.finding.finding import Finding
from ..helpers.data_loader import load_data
from ..helpers.default_objects import generate_patient, generate_gender

import random

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


COLO_AUSTRIA_EXAMINATION_NAME = "colonoscopy_austria_screening"
REQ_SET_NAME = "colonoscopy_austria_screening_qa"
LINKED_SET_NAMES = [
    "colonoscopy_austria_screening_finding_polyp_required_classifications",
    "colonoscopy_austria_screening_required_patient_information"
]

GENDER_UNKNOWN_NAME = "unknown"


class RequirementSetColoAustriaTest(TestCase):

    def setUp(self):
        """
        Sets up initial test data for all tests in the class.
        
        Loads fixture data and ensures required units, lab value definitions with normal ranges, and gender objects exist for use in requirement set evaluation tests.
        """
        load_data() # Load all YAML data including RequirementSets, Requirements, LabValues etc.
        self.req_set = RequirementSet.objects.get(name=REQ_SET_NAME)
        self.assertIsNotNone(self.req_set, f"RequirementSet '{REQ_SET_NAME}' should exist.")

        self.patient = generate_patient()
        self.patient.save()

        self.base_examination = self.patient.create_examination(
            examination_name_str=COLO_AUSTRIA_EXAMINATION_NAME,
            date_start=timezone.now(),
            date_end=timezone.now() + datetime.timedelta(minutes=30)
        )

        self.base_examination.save()

    def test_linked_sets(self):
        """
        Verifies that the "basic_lab_values_normal" RequirementSet evaluates to True when all associated lab values for a patient are within normal ranges.
        """
        req_set = self.req_set
        all_req_sets = req_set.all_linked_sets
        linked_req_set_names = [req_set.name for req_set in all_req_sets]

        for req_linked_set_name in LINKED_SET_NAMES:
            self.assertIn(req_linked_set_name, linked_req_set_names, f"RequirementSet '{req_linked_set_name}' should be linked to '{REQ_SET_NAME}'.")


    def test_colonoscopy_austria_screening_required_patient_information(self):
        """
        Tests if the 'colonoscopy_austria_screening_required_patient_information' RequirementSet evaluates to True when all required patient information is provided.
        """
        req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_required_patient_information")
        self.assertIsNotNone(req_set, "RequirementSet 'colonoscopy_austria_screening_required_patient_information' should exist.")

        result = req_set.evaluate(self.patient)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_required_patient_information' should evaluate to True when all required patient information is provided.")
        

        gender_unknown = Gender.objects.get(name=GENDER_UNKNOWN_NAME)
        self.patient.gender = gender_unknown
        self.patient.save()

        result = req_set.evaluate(self.patient)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_required_patient_information' should evaluate to False when required patient information is missing.")

    def test_colonoscopy_austria_screening_finding_polyp_required_classifications(self):
        """
        Tests if the 'colonoscopy_austria_screening_finding_polyp_required_classifications' RequirementSet evaluates to True when all required classifications for the 'colon_polyp' finding are present.
        """
        req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_finding_polyp_required_classifications")
        self.assertIsNotNone(req_set, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should exist.")

        # Create a colon polyp finding without any classifications
        colon_polyp_finding = Finding.objects.get(name="colon_polyp")
        self.assertIsNotNone(colon_polyp_finding, "Finding 'colon_polyp' should exist.")
        patient_colon_polyp_finding = self.base_examination.create_finding(colon_polyp_finding)

        result = req_set.evaluate(patient_colon_polyp_finding)
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should evaluate to False when no classifications are present.")

        # add required classifications
        ## colon_lesion_size_austria_categorical
        _classification = colon_polyp_finding.finding_classifications.get(name="colon_lesion_size_austria_categorical")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)

        ## colon_austria_screening_polyp_size
        _classification = colon_polyp_finding.finding_classifications.get(name="colonoscopy_location_default")
        _choices = _classification.choices.all()
        ### random choice from choices
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)

        ## colon_lesion_planarity_default
        _classification = colon_polyp_finding.finding_classifications.get(name="colon_lesion_planarity_default")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)

        ## colonoscopy_polyp_histology
        _classification = colon_polyp_finding.finding_classifications.get(name="colonoscopy_polyp_histology")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)

        ## colonoscopy_polyp_histology_dysplasia
        _classification = colon_polyp_finding.finding_classifications.get(name="colonoscopy_polyp_histology_dysplasia")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)

        # should still evaluate to False
        result = req_set.evaluate(patient_colon_polyp_finding)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should evaluate to False when not all required classifications are present.")

        ## colonoscopy_histology_dignity
        _classification = colon_polyp_finding.finding_classifications.get(name="colonoscopy_histology_dignity")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_colon_polyp_finding.add_classification(_classification.pk, _choice.pk)
        
        result = req_set.evaluate(patient_colon_polyp_finding)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should evaluate to True when all required classifications are present.")

        # Now lets test by passing the PatientExamination directly
        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should evaluate to True when all required classifications are present in the PatientExamination.")


        _bowel_prep_finding = Finding.objects.get(name="bowel_prep_screening_austria")
        self.assertIsNotNone(_bowel_prep_finding, "Finding 'bowel_prep_screening_austria' should exist.")
        patient_bowel_prep_finding = self.base_examination.create_finding(_bowel_prep_finding)

        # add bowel prep classification
        _classification = _bowel_prep_finding.finding_classifications.get(name="bowel_prep_screening_austria")
        _choices = _classification.choices.all()
        _choice = random.choice(_choices)
        patient_bowel_prep_finding.add_classification(_classification.pk, _choice.pk)

        # should still evaluate to True
        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_finding_polyp_required_classifications' should evaluate to True when all required classifications are present in the PatientExamination.")


    def test_colonoscopy_austria_screening_examination_complete(self):
        """
        Tests if the 'colonoscopy_austria_screening_required_findings' RequirementSet evaluates to True when all required findings for the colonoscopy examination are present.
        """
        req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_examination_complete")
        self.assertIsNotNone(req_set, "RequirementSet 'colonoscopy_austria_screening_examination_required_findings' should exist.")

        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_examination_required_findings' should evaluate to False when no findings are present.")

        # Create finding Cecum and add finding classification visualized with choice "yes"
        cecum_finding = Finding.objects.get(name="cecum")
        self.assertIsNotNone(cecum_finding, "Finding 'cecum' should exist.")
        patient_cecum_finding = self.base_examination.create_finding(cecum_finding)
        
        # add required classification
        _classification = cecum_finding.finding_classifications.get(name="visualized")
        _choice = _classification.choices.get(name="yes")
        patient_cecum_finding.add_classification(_classification.pk, _choice.pk)

        # Should now evaluate to True
        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_examination_required_findings' should evaluate to True when required findings are present in the PatientExamination.")

        # now lets add a terminal_ileum finding with visualized and "no"
        terminal_ileum_finding = Finding.objects.get(name="terminal_ileum")
        self.assertIsNotNone(terminal_ileum_finding, "Finding 'terminal_ileum' should exist.")
        patient_terminal_ileum_finding = self.base_examination.create_finding(terminal_ileum_finding)
        
        # add required classification
        _classification = terminal_ileum_finding.finding_classifications.get(name="visualized")
        _choice = _classification.choices.get(name="no")
        patient_terminal_ileum_finding.add_classification(_classification.pk, _choice.pk)

        # Should still evaluate to True
        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertTrue(result, "RequirementSet 'colonoscopy_austria_screening_examination_required_findings' should evaluate to True when required findings are present in the PatientExamination.")

    def test_colonoscopy_austria_screening_cecum_not_visualized(self):
        """
        Tests if the 'colonoscopy_austria_screening_cecum_not_visualized' RequirementSet evaluates to True when the cecum finding is not visualized.
        """
        req_set = RequirementSet.objects.get(name="colonoscopy_austria_screening_examination_complete")
        self.assertIsNotNone(req_set, "RequirementSet 'colonoscopy_austria_screening_examination_complete' should exist.")

        # Create finding Cecum and add finding classification visualized with choice "no"
        cecum_finding = Finding.objects.get(name="cecum")
        self.assertIsNotNone(cecum_finding, "Finding 'cecum' should exist.")
        patient_cecum_finding = self.base_examination.create_finding(cecum_finding)
        
        # add required classification
        _classification = cecum_finding.finding_classifications.get(name="visualized")
        _choice = _classification.choices.get(name="no")
        patient_cecum_finding.add_classification(_classification.pk, _choice.pk)

        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_examination_complete' should evaluate to False when cecum finding is not visualized.")

        # now lets add finding "sigmoid_colon" with visualized "yes"
        sigmoid_colon_finding = Finding.objects.get(name="sigmoid_colon")
        self.assertIsNotNone(sigmoid_colon_finding, "Finding 'sigmoid_colon' should exist.")
        patient_sigmoid_colon_finding = self.base_examination.create_finding(sigmoid_colon_finding)
        
        # add required classification
        _classification = sigmoid_colon_finding.finding_classifications.get(name="visualized")
        _choice = _classification.choices.get(name="yes")
        patient_sigmoid_colon_finding.add_classification(_classification.pk, _choice.pk)
        
        # Should still evaluate to False
        result = req_set.evaluate(self.base_examination)
        self.assertIsInstance(result, bool, "RequirementSet evaluation should return a boolean value.")
        self.assertFalse(result, "RequirementSet 'colonoscopy_austria_screening_examination_complete' should evaluate to False when cecum finding is not visualized.")