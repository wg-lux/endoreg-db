from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    Requirement, 
)

import logging

from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient
)

logger = getLogger(__name__)
logger.setLevel(logging.WARNING)


class RequirementTest(TestCase):
    def setUp(self):  
        load_data()

        self.patient = generate_patient()
        self.patient.save()

        self.requirements = Requirement.objects.all()
        self.assertGreater(len(self.requirements), 0, "No requirements found. Check data fixtures.")

    def test_requirements_have_type(self):
        # fetch all requirements and make sure they are linked to at least one requirement_type
        for req in self.requirements:
            logger.info(f"Testing requirement: {req.name}")
            self.assertTrue(req.requirement_types.exists(), 
                            f"Requirement '{req.name}' should have a linked RequirementType. Check data fixtures.")
