
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


from ...helpers.data_loader import (
    load_data
)

from ...helpers.default_objects import (
    get_default_center, 
    get_gender_m_or_f,
    generate_patient
)

logger = getLogger(__name__)
logger.setLevel(logging.WARNING)

req_name_bleeding_high = "endoscopy_intervention_bleeding_risk_high"
req_name_bleeding_low = "endoscopy_intervention_bleeding_risk_low"



class RequirementTest(TestCase):
    def setUp(self):  
        load_data()

        self.req_bleeding_high = Requirement.objects.get(name=req_name_bleeding_high)
        self.assertIsInstance(self.req_bleeding_high, Requirement)
        self.req_bleeding_low = Requirement.objects.get(name=req_name_bleeding_low)
        self.assertIsInstance(self.req_bleeding_low, Requirement)

        self.indication_screening_colo = ExaminationIndication.objects.get(
            name="colonoscopy_screening"
        )

        self.patient = generate_patient()
        self.patient.save()

    def test_links_requirement(self):
        """
        Test the links of the requirements.
        """
        # Check if the requirement has links
        from endoreg_db.utils.links.requirement_link import RequirementLinks
    
        requirement_links_high = self.req_bleeding_high.links
        self.assertIsInstance(requirement_links_high, RequirementLinks)

        examination_indications = requirement_links_high.examination_indications
        print(requirement_links_high)
        self.assertIsInstance(examination_indications, list)
        

    def test_match_any(self):
        """
        Test the match_any method of the RequirementLinks class.
        """

        requirement_links_high = self.req_bleeding_high.links
        requirement_links_low = self.req_bleeding_low.links

        # Check if the match_any method works correctly
        is_match = requirement_links_high.match_any(requirement_links_low)
        self.assertFalse(is_match, "The match_any method should return False for non-matching requirements.")

        is_match = requirement_links_high.match_any(requirement_links_high)
        self.assertTrue(is_match, "The match_any method should return True for matching requirements.")
