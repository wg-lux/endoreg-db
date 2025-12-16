from django.test import TestCase
from logging import getLogger

from endoreg_db.models import (
    Requirement, 
    PatientDisease,
    Disease,
)
from endoreg_db.utils.links.requirement_link import RequirementLinks # Added RequirementLinks

import logging

from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient
)

logger = getLogger(__name__)
logger.setLevel(logging.INFO) # Changed to INFO for more verbose logging during test development


class RequirementDiseaseClassificationChoiceTest(TestCase):
    def setUp(self):
        load_data()

        self.patient = generate_patient()
        self.patient.save()

        # Create a base disease (e.g., chronic_kidney_disease)
        self.base_disease = Disease.objects.get(name="chronic_kidney_disease")
        
        # Create a PatientDisease instance for the patient
        self.patient_disease_instance = PatientDisease.objects.create(
            patient=self.patient,
            disease=self.base_disease
        )

        req_name_starts_with = "disease_classification_choice_is"
        self.requirements = Requirement.objects.filter(name__startswith=req_name_starts_with)
        self.assertGreater(len(self.requirements), 0, f"No requirements found starting with '{req_name_starts_with}'. Check data fixtures (e.g., disease_renal.yaml).")

    def test_disease_classification_choice_is_requirements(self):
        # Test with Patient object as input
        self._run_classification_choice_test_with_input(self.patient)
        # Test with PatientDisease object as input
        self._run_classification_choice_test_with_input(self.patient_disease_instance)

    def _run_classification_choice_test_with_input(self, input_object):
        input_type_name = type(input_object).__name__
        logger.info(f"Running test_disease_classification_choice_is_requirements with input type: {input_type_name}")

        self.assertTrue(hasattr(input_object, 'links'), f"{input_type_name} model should have a 'links' property.")
        self.assertIsInstance(input_object.links, RequirementLinks, f"{input_type_name}.links should return a RequirementLinks object.")

        for req in self.requirements:
            logger.info(f"Testing requirement: {req.name} against {input_type_name}")
            
            # Ensure the requirement expects the correct type or a compatible one (Patient or PatientDisease)
            self.assertTrue(
                any(isinstance(input_object, model_type) for model_type in req.expected_models),
                f"Requirement '{req.name}' (expected: {req.expected_models}) is not configured for input type '{input_type_name}'. Check RequirementType in fixtures."
            )

            # 1. With no specific classification choices linked to PatientDisease, validation should fail
            self.patient_disease_instance.classification_choices.clear()
            self.assertEqual(self.patient_disease_instance.classification_choices.count(), 0, 
                             f"PatientDisease should have no classification choices at the start of this test iteration for {input_type_name}.")
            
            is_fulfilled_no_choice = req.evaluate(input_object, mode="loose")
            self.assertFalse(is_fulfilled_no_choice, 
                             f"Requirement '{req.name}' should NOT be fulfilled when PatientDisease has no specific classification choices. Input: {input_type_name}, Patient links: {self.patient.links.active()}, PD links: {self.patient_disease_instance.links.active()}, Req links: {req.links.active()}")

            # 2. Fetch all disease classification choices from the requirement
            required_choices = req.disease_classification_choices.all()
            self.assertGreater(len(required_choices), 0, 
                               f"Requirement '{req.name}' has no disease_classification_choices linked to it. Check data fixtures.")

            # 3. Add the required classification choices to the PatientDisease instance
            for choice in required_choices:
                self.patient_disease_instance.classification_choices.add(choice)
            
            self.assertEqual(self.patient_disease_instance.classification_choices.count(), len(required_choices), 
                             f"PatientDisease should now have {len(required_choices)} classification choices linked for input {input_type_name}.")

            # 4. Verify that evaluation now returns true
            is_fulfilled_with_choice = req.evaluate(input_object, mode="loose")
            self.assertTrue(is_fulfilled_with_choice, 
                            f"Requirement '{req.name}' SHOULD be fulfilled when PatientDisease has the required classification choice(s). Input: {input_type_name}, Patient links: {self.patient.links.active()}, PD links: {self.patient_disease_instance.links.active()}, Req links: {req.links.active()}")
            
            # Clean up choices for the next iteration
            self.patient_disease_instance.classification_choices.clear()
