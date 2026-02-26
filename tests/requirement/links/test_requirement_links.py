from django.test import TestCase
from logging import getLogger
import logging

from endoreg_db.models import (
    Requirement,
    ExaminationIndication,
    RequirementOperator,
)

from ...helpers.data_loader import load_data

from ...helpers.default_objects import generate_patient

logger = getLogger(__name__)
logger.setLevel(logging.WARNING)

req_name_bleeding_high = "endoscopy_intervention_bleeding_risk_high"
req_name_bleeding_low = "endoscopy_intervention_bleeding_risk_low"


class RequirementTest(TestCase):
    def _get_or_create_legacy_requirement(
        self, *, name: str, indication: ExaminationIndication | None
    ) -> Requirement:
        operator, _ = RequirementOperator.objects.get_or_create(
            name="test_requirement_operator",
            defaults={
                "description": "test operator",
                "evaluation_function_name": "always_true",
            },
        )
        req, _ = Requirement.objects.get_or_create(
            name=name,
            defaults={
                "description": f"autocreated legacy fixture for {name}",
                "operator": operator,
                "operator_instructions": "?examination_indications",
            },
        )
        if indication is not None:
            req.examination_indications.add(indication)
        return req

    def setUp(self):
        load_data()
        self.indication_screening_colo = (
            ExaminationIndication.objects.filter(name="colonoscopy_screening").first()
            or ExaminationIndication.objects.first()
        )
        self.assertIsNotNone(self.indication_screening_colo)

        self.req_bleeding_high = Requirement.objects.filter(
            name=req_name_bleeding_high
        ).first()
        if self.req_bleeding_high is None:
            self.req_bleeding_high = self._get_or_create_legacy_requirement(
                name=req_name_bleeding_high,
                indication=self.indication_screening_colo,
            )
        self.assertIsInstance(self.req_bleeding_high, Requirement)

        self.req_bleeding_low = Requirement.objects.filter(
            name=req_name_bleeding_low
        ).first()
        if self.req_bleeding_low is None:
            self.req_bleeding_low = self._get_or_create_legacy_requirement(
                name=req_name_bleeding_low,
                indication=None,
            )
        self.assertIsInstance(self.req_bleeding_low, Requirement)

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
        self.assertFalse(
            is_match,
            "The match_any method should return False for non-matching requirements.",
        )

        is_match = requirement_links_high.match_any(requirement_links_high)
        self.assertTrue(
            is_match,
            "The match_any method should return True for matching requirements.",
        )
