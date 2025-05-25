from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    Requirement, 
    PatientDisease, # Added PatientDisease
    Disease,        # Added Disease
    Patient         # Added Patient
)
from endoreg_db.utils.links.requirement_link import RequirementLinks

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

        req_name_starts_with = "disease_is"
        self.requirements = Requirement.objects.filter(name__startswith=req_name_starts_with)
        self.assertGreater(len(self.requirements), 0, f"No requirements found starting with '{req_name_starts_with}'. Check data fixtures.")

    def test_disease_is_requirements(self):
        self.assertTrue(hasattr(self.patient, 'links'), "Patient model should have a 'links' property.")
        self.assertIsInstance(self.patient.links, RequirementLinks, "Patient.links should return a RequirementLinks object.")

        for req in self.requirements:
            logger.info(f"Testing requirement: {req.name}")
            self.assertIn(Patient, req.expected_models, 
                            f"Requirement '{req.name}' should expect Patient as an input model type. Check RequirementType in fixtures.")

            # 1. With no diseases, validation should fail
            # Ensure patient has no diseases for this part of the test
            PatientDisease.objects.filter(patient=self.patient).delete()
            self.assertEqual(self.patient.diseases.count(), 0, "Patient should have no diseases at the start of this test iteration.")
            
            is_fulfilled_no_disease = req.evaluate(self.patient, mode="loose")
            self.assertFalse(is_fulfilled_no_disease, 
                             f"Requirement '{req.name}' should NOT be fulfilled when patient has no diseases. Patient links: {self.patient.links.active()}, Req links: {req.links.active()}")

            # 2. Fetch all diseases from the requirement
            required_diseases = req.diseases.all()
            self.assertGreater(len(required_diseases), 0, 
                               f"Requirement '{req.name}' has no diseases linked to it. Check data fixtures.")

            # 3. Create the corresponding patient_disease objects
            for disease in required_diseases:
                PatientDisease.objects.create(patient=self.patient, disease=disease)
            
            self.assertEqual(self.patient.diseases.count(), len(required_diseases), 
                             f"Patient should now have {len(required_diseases)} diseases linked.")

            # 4. Verify that "validate" now returns true
            is_fulfilled_with_disease = req.evaluate(self.patient, mode="loose")
            self.assertTrue(is_fulfilled_with_disease, 
                            f"Requirement '{req.name}' SHOULD be fulfilled when patient has the required disease(s). Patient links: {self.patient.links.active()}, Req links: {req.links.active()}")
            
            # Clean up PatientDisease records for the next iteration if multiple "disease_is" requirements exist
            PatientDisease.objects.filter(patient=self.patient).delete()


