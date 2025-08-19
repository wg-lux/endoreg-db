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

# Dict Layout:
## FINDING_NAME_1: {
##     "FINDING_CLASSIFICATION_1": [
##         "FINDING_CLASSIFICATION_CHOICE_1",
##         "FINDING_CLASSIFICATION_CHOICE_2",
##     ],
##     "FINDING_CLASSIFICATION_2": [
##         "FINDING_CLASSIFICATION_CHOICE_3",
##     ],
## }

# REQUIRED_FINDING_DICT = {
# 'cecum', 
# 'terminal_ileum', 
# 'ileocecal_valve', 
# 'ascending_colon', 
# 'right_flexure', 
# 'transverse_colon', 
# 'left_flexure', 
# 'descending_colon', 
# 'sigmoid_colon', 
# 'rectum', 
# 'rectum_anus', 
# 'colonoscopy_complete', 
# 'colonoscopy_austria_screening_findings_summary', 
# 'bowel_prep_screening_austria', 
# 'complication', 
# 'examination_setting', 
# 'sedation', 
# 'colonoscopy_austria_screening_outcome', 
# "colon_polyp"
# }

REQUIRED_FINDING_DICT =  {
    "cecum": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "terminal_ileum": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "ileocecal_valve": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "ascending_colon": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "right_flexure": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "transverse_colon": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "left_flexure": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "descending_colon": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "sigmoid_colon": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "rectum": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "rectum_anus": {
        "visualized": [
            "yes",
            "no",
        ],
    },
    "colonoscopy_complete": {  
        "colonoscopy_not_complete_reason": [
            "colonoscopy_incomplete_insufficient_preparation",
            "colonoscopy_incomplete_complication",
            "colonoscopy_incomplete_pain",
            "colonoscopy_incomplete_stenosis",
            "colonoscopy_incomplete_other",
        ],
    },
    "colonoscopy_austria_screening_findings_summary": {
        "colonoscopy_austria_screening_findings_summary_worst": [
            "colonoscopy_worst_finding_colon_cancer",
            "colonoscopy_worst_finding_other",
            "colonoscopy_worst_finding_polyp",
            "colonoscopy_worst_finding_rectum_cancer",
        ],
    },
    "colonoscopy_austria_screening_outcome": {
        "colonoscopy_screening_outcome_overall": [
            "outcome_restitutio_ad_integrum",
            "outcome_death",
            "outcome_unknown",
            "outcome_lasting_disability",
        ],
    },
    "examination_setting": {
        "colonoscopy_austria_screening_final_examination_setting": [
            "examination_setting_generic_location_inpatient",
            "examination_setting_generic_location_outpatient",
            "examination_setting_operative",
            "examination_setting_conservative"
        ]
    },
    "bowel_prep_screening_austria": {
        "bowel_prep_screening_austria": [
            "bowel_prep_perfect",
            "bowel_prep_good",
            "bowel_prep_fair",
            "bowel_prep_poor",
            "bowel_prep_poor_in_rc_only"
        ]
    },
    "colon_polyp": {
        "colon_lesion_size_austria_categorical": [
            "colon_lesion_size_austria_categorical_0_5",
            "colon_lesion_size_austria_categorical_6_9",
            "colon_lesion_size_austria_categorical_10_20",
            "colon_lesion_size_austria_categorical_21_x"
        ],
        "colonoscopy_austria_screening_location": [
            "left_colon",
            "right_colon",
            "sigmoid_colon",
            "rectum",
        ],
        "colonoscopy_polyp_histology": [
            "histology_polyp_hyperplastic",
            "histology_polyp_adenoma_tubular",
            "histology_polyp_adenoma_tubulovillous",
            "histology_polyp_adenoma_villous",
            "histology_polyp_sessile_serrated_lesion"
        ],
        "colonoscopy_polyp_histology_dysplasia": [
            "histology_dysplasia_none",
            "histology_dysplasia_low_grade",
            "histology_dysplasia_high_grade",
        ],
        "colonoscopy_histology_dignity": [
            "histology_dignity_benign",
            "histology_dignity_malign",
        ]
    },

}

class ColonoscopyFindingTest(TestCase):
    
    # @classmethod
    # def setUpTestData(cls):
    def setUp(self):
        """
        Initializes test data for the Austria Structured Screening Colonoscopy.
        
        Creates and retrieves Finding instances.
        """
        load_data()
        
        self.colonoscopy = Examination.objects.get(name="colonoscopy_austria_screening")
        # self.colonoscopy = Examination.objects.get(name="colonoscopy")

        self.colo_examination_indications = self.colonoscopy.indications.all()
        
        # cls.colonoscopy.findings is a ManyToManyField, so we need to filter by the examination
        self.colo_findings = self.colonoscopy.findings.all()

    def test_colonoscopy_screening_austria_findings(self):
        """
        Test if the colonoscopy findings are correctly associated with the colonoscopy examination.
        
        Checks if the findings related to colonoscopy are correctly linked to the colonoscopy examination.
        """
        logger.info("Testing colonoscopy findings")
        
        finding_names = [finding.name for finding in self.colo_findings]
        missing_elements = {
            'findings': [],
            'classifications': [],
            'choices': []
        }
        
        # Check if all required findings are present and collect missing elements
        for finding_name, classifications_with_choices in REQUIRED_FINDING_DICT.items():
            if finding_name not in finding_names:
                missing_elements['findings'].append(finding_name)
                continue
        
            # Check if all required classifications and choices are present
            for classification_name, choices in classifications_with_choices.items():
                try:
                    classification = self.colo_findings.get(name=finding_name).finding_classifications.get(name=classification_name)
                except Exception:
                    missing_elements['classifications'].append(f"'{classification_name}' classification for '{finding_name}' finding")
                    continue
                
                classification_choice_names = [choice.name for choice in classification.choices.all()]
                for choice_name in choices:
                    if choice_name not in classification_choice_names:
                        missing_elements['choices'].append(f"'{choice_name}' choice for '{classification_name}' classification in '{finding_name}' finding")
        # Report all missing elements at once
        error_messages = []
        if missing_elements['findings']:
            error_messages.append(f"Missing findings: {', '.join(missing_elements['findings'])}")
        if missing_elements['classifications']:
            error_messages.append(f"Missing classifications: {', '.join(missing_elements['classifications'])}")
        if missing_elements['choices']:
            error_messages.append(f"Missing choices: {', '.join(missing_elements['choices'])}")
        
        if error_messages:
            self.fail("Colonoscopy findings validation failed:\n" + "\n".join(error_messages))

