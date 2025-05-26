from django.test import TestCase
from logging import getLogger
import logging

from endoreg_db.models import (
    Requirement,
    PatientLabValue,
    RequirementOperator, # Added RequirementOperator import
)
# Corrected import paths
from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient,
    generate_gender # Added generate_gender import
)

logger = getLogger(__name__)
logger.setLevel(logging.WARNING)


class RequirementLabValueLatestTest(TestCase):
    OPERATOR_NAMES = [
        "lab_latest_numeric_increased",
        "lab_latest_numeric_decreased",
        "lab_latest_numeric_normal",
        "lab_latest_numeric_lower_than_value",
        "lab_latest_numeric_greater_than_value",
        "lab_latest_numeric_increased_factor_in_timeframe",
        "lab_latest_numeric_decreased_factor_in_timeframe",
        "lab_latest_numeric_normal_in_timeframe",
        "lab_latest_numeric_lower_than_value_in_timeframe",
        "lab_latest_numeric_greater_than_value_in_timeframe",
        "lab_latest_categorical_match",
        "lab_latest_categorical_match_substring",
        "lab_latest_categorical_match_regex",
        "lab_latest_categorical_match_in_timeframe",
        "lab_latest_categorical_match_substring_in_timeframe",
        "lab_latest_categorical_match_regex_in_timeframe",
    ]

    def setUp(self):
        """
        Initializes test data and verifies the existence of required operators and requirements.
        
        Loads lab value test data, creates patient instances for each gender category, and ensures that all operators in OPERATOR_NAMES exist in the database. Builds a mapping of operator names to their associated Requirement instances for use in test methods.
        """
        load_data() # This should load the new examples from lab_value.yaml

        self.male_patient = generate_patient(gender=generate_gender(name="male"))
        self.male_patient.save()
        self.female_patient = generate_patient(gender=generate_gender(name="female"))
        self.female_patient.save()
        self.unknown_gender_patient = generate_patient(gender=generate_gender()) # No gender
        self.unknown_gender_patient.save()

        self.patients = {
            "male": self.male_patient,
            "female": self.female_patient,
            "unknown": self.unknown_gender_patient
        }

        self.requirements_map = {}
        for op_name in self.OPERATOR_NAMES:
            try:
                # Ensure the operator itself exists in the database
                RequirementOperator.objects.get(name=op_name)
            except RequirementOperator.DoesNotExist:
                raise AssertionError(
                    f"RequirementOperator '{op_name}' (from OPERATOR_NAMES list in test setup) "
                    f"was not found in the database. Check 'lab_operators.yaml' and data loading."
                )

            # Fetch all Requirement models that use this operator by its name
            related_requirements = Requirement.objects.filter(operators__name=op_name)
            
            self.requirements_map[op_name] = list(related_requirements)


    def test_lab_value_latest_requirements_exist(self):
        # This test verifies that every operator listed in OPERATOR_NAMES
        # has at least one Requirement model in 'lab_value.yaml' that uses it.

        operators_without_requirements = []

        for op_name in self.OPERATOR_NAMES:
            self.assertIn(op_name, self.requirements_map,
                          f"Operator '{op_name}' was expected but not found in self.requirements_map. "
                          f"This indicates an issue in the setUp method logic beyond simple operator existence.")

            requirements_for_operator = self.requirements_map[op_name]

            if not requirements_for_operator:
                operators_without_requirements.append(op_name)
            else:
                for req in requirements_for_operator:
                    self.assertIsInstance(req, Requirement,
                                          f"For operator '{op_name}', expected a list of Requirement instances, "
                                          f"but found an item of type {type(req)}: {req}")
                    self.assertTrue(req.operators.filter(name=op_name).exists(),
                                    f"Requirement '{req.name}' is mapped to operator '{op_name}', "
                                    f"but its 'operators' field does not actually contain a link to '{op_name}'. "
                                    f"Check data consistency or the query in setUp.")

        self.assertEqual(len(operators_without_requirements), 0,
                         f"The following operators from OPERATOR_NAMES do not have any associated Requirement "
                         f"instances loaded from 'lab_value.yaml': {operators_without_requirements}. "
                         f"Please add corresponding Requirement fixtures for them in 'endoreg_db/data/requirement/lab_value.yaml'.")

    def test_lab_value_latest_increased_requirements_patient(self):
        """
        Verifies that requirements using the 'lab_latest_numeric_increased' operator are fulfilled for patients of all genders.
        
        For each patient gender, this test creates lab samples and increased lab values, then asserts that the associated requirements are fulfilled when evaluated with the lab value, the patient, and the lab sample as input.
        """
        operator_name = "lab_latest_numeric_increased"
        increased_requirements = self.requirements_map.get(operator_name, [])
        self.assertTrue(increased_requirements, f"No requirements found for operator \'{operator_name}\'. Check fixtures.")

        for gender_name, patient in self.patients.items():
            with self.subTest(gender=gender_name, patient_id=patient.pk):
                for requirement in increased_requirements:
                    self.assertIsInstance(requirement, Requirement)
                    
                    lab_values = requirement.links.lab_values
                    self.assertTrue(lab_values, f"Requirement \'{requirement.name}\' for operator \'{operator_name}\' has no linked lab_values.")

                    pls = patient.create_lab_sample()
                    pls.save()

                    for lab_value in lab_values:
                        # Generate increased value based on patient's gender
                        increased_value = lab_value.get_increased_value(patient=patient)
                        
                        self.assertIsNotNone(
                            increased_value, 
                            f"Could not generate an increased value for lab value '{lab_value.name}' (Gender: {gender_name}). "
                            "This might be due to a missing normal range or numerical distribution definition for this lab value."
                        )

                        plv = PatientLabValue.create_lab_value_by_sample(
                            sample=pls,
                            lab_value_name=lab_value.name,
                            value=increased_value,
                            unit=lab_value.default_unit 
                        )
                        plv.save()

                        input_object = plv
                        is_fulfilled = requirement.evaluate(input_object, mode="loose")
                        self.assertTrue(
                            is_fulfilled, 
                            f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for lab value {lab_value} with input {input_object} (increased). "
                            "This might be due to a missing normal range or numerical distribution definition for the lab value."
                        )

                    pls.refresh_from_db()
                    patient.refresh_from_db()

                    input_object = patient
                    is_fulfilled_with_patient = requirement.evaluate(input_object, mode="loose")
                    self.assertTrue(
                        is_fulfilled_with_patient, 
                        f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for patient {patient} with input {input_object} (increased). "
                        "This might be due to a missing normal range or numerical distribution definition for the lab value."
                    )

                    input_object = pls
                    is_fulfilled_with_sample = requirement.evaluate(input_object, mode="loose")
                    self.assertTrue(
                        is_fulfilled_with_sample, 
                        f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for lab sample {pls} with input {input_object} (increased). "
                        "This might be due to a missing normal range or numerical distribution definition for the lab value."
                    )

    def test_lab_value_latest_decreased_requirements_patient(self):
        """
        Verifies that requirements using the 'lab_latest_numeric_decreased' operator are fulfilled for patients of all genders.
        
        For each patient gender, this test creates lab samples and decreased lab values, then checks that all associated requirements are correctly evaluated as fulfilled when provided with the lab value, the patient, or the lab sample as input.
        """
        operator_name = "lab_latest_numeric_decreased"
        decreased_requirements = self.requirements_map.get(operator_name, [])
        self.assertTrue(decreased_requirements, f"No requirements found for operator \'{operator_name}\'. Check fixtures.")

        for gender_name, patient in self.patients.items():
            with self.subTest(gender=gender_name, patient_id=patient.pk):
                for requirement in decreased_requirements:
                    self.assertIsInstance(requirement, Requirement)
                    
                    lab_values = requirement.links.lab_values
                    self.assertTrue(lab_values, f"Requirement \'{requirement.name}\' for operator \'{operator_name}\' has no linked lab_values.")

                    pls = patient.create_lab_sample()
                    pls.save()

                    for lab_value in lab_values:
                        decreased_value = lab_value.get_decreased_value(patient=patient)
                        
                        self.assertIsNotNone(
                            decreased_value, 
                            f"Could not generate a decreased value for lab value '{lab_value.name}' (Gender: {gender_name}). "
                            "This might be due to a missing normal range or numerical distribution definition for this lab value."
                        )

                        plv = PatientLabValue.create_lab_value_by_sample(
                            sample=pls,
                            lab_value_name=lab_value.name,
                            value=decreased_value,
                            unit=lab_value.default_unit 
                        )
                        plv.save()

                        input_object = plv
                        is_fulfilled = requirement.evaluate(input_object, mode="loose")
                        self.assertTrue(
                            is_fulfilled, 
                            f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for lab value {lab_value} with input {input_object} (decreased). "
                            "This might be due to a missing normal range or numerical distribution definition for the lab value."
                        )

                    pls.refresh_from_db()
                    patient.refresh_from_db()

                    input_object = patient
                    is_fulfilled_with_patient = requirement.evaluate(input_object, mode="loose")
                    self.assertTrue(
                        is_fulfilled_with_patient, 
                        f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for patient {patient} with input {input_object} (decreased). "
                        "This might be due to a missing normal range or numerical distribution definition for the lab value."
                    )

                    input_object = pls
                    is_fulfilled_with_sample = requirement.evaluate(input_object, mode="loose")
                    self.assertTrue(
                        is_fulfilled_with_sample, 
                        f"Requirement '{requirement.name}' (Gender: {gender_name}) was not fulfilled for lab sample {pls} with input {input_object} (decreased). "
                        "This might be due to a missing normal range or numerical distribution definition for the lab value."
                    )

    def test_lab_value_latest_normal_requirements_patient(self):
        """
        Verifies that requirements using the 'lab_latest_numeric_normal' operator are fulfilled for patients of all genders.
        
        For each patient gender, this test creates lab samples and normal lab values, then asserts that the corresponding requirements are fulfilled when evaluated with the generated lab values, the patient, and the lab sample.
        """
        operator_name = "lab_latest_numeric_normal"
        requirements_for_operator = self.requirements_map.get(operator_name)
        self.assertTrue(requirements_for_operator, f"No requirements found for operator '{operator_name}'. Check fixtures.")

        for gender_name, patient in self.patients.items():
            with self.subTest(gender=gender_name, patient_id=patient.pk):
                for requirement in requirements_for_operator:
                    self.assertIsInstance(requirement, Requirement)
                    lab_values_linked = requirement.links.lab_values
                    self.assertTrue(lab_values_linked, f"Requirement '{requirement.name}' for operator '{operator_name}' has no linked lab_values.")

                    pls = patient.create_lab_sample()
                    pls.save()

                    for lab_value_model in lab_values_linked:
                        normal_value = lab_value_model.get_normal_value(patient=patient)
                        self.assertIsNotNone(normal_value, f"Could not generate a normal value for lab value '{lab_value_model.name}' (Gender: {gender_name}). Check its normal range and distribution definitions.")

                        plv = PatientLabValue.create_lab_value_by_sample(
                            sample=pls,
                            lab_value_name=lab_value_model.name,
                            value=normal_value,
                            unit=lab_value_model.default_unit
                        )
                        plv.save()

                        self.assertTrue(requirement.evaluate(plv, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PLV {plv} with normal value.")
                    
                    self.assertTrue(requirement.evaluate(patient, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for Patient with normal value.")
                    self.assertTrue(requirement.evaluate(pls, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PatientLabSample with normal value.")

    def test_lab_value_latest_lower_than_value_requirements_patient(self):
        """
        Verifies that requirements using the 'lab_latest_numeric_lower_than_value' operator are fulfilled for patients of all genders when lab values are set just below the defined threshold.
        
        For each patient gender, this test creates lab samples and lab values slightly less than the requirement's numeric threshold, then asserts that the requirement is fulfilled for the lab value, the patient, and the lab sample.
        """
        operator_name = "lab_latest_numeric_lower_than_value"
        requirements_for_operator = self.requirements_map.get(operator_name)
        self.assertTrue(requirements_for_operator, f"No requirements found for operator '{operator_name}'. Check fixtures.")

        for gender_name, patient in self.patients.items():
            with self.subTest(gender=gender_name, patient_id=patient.pk):
                for requirement in requirements_for_operator:
                    self.assertIsNotNone(requirement.numeric_value, f"Requirement '{requirement.name}' for '{operator_name}' must have a numeric_value defined.")
                    threshold = requirement.numeric_value
                    test_value = threshold - (1 if threshold > 0.1 else 0.01) 

                    lab_values_linked = requirement.links.lab_values
                    self.assertTrue(lab_values_linked, f"Requirement '{requirement.name}' for operator '{operator_name}' has no linked lab_values.")
                    pls = patient.create_lab_sample()
                    pls.save()

                    for lab_value_model in lab_values_linked:
                        plv = PatientLabValue.create_lab_value_by_sample(
                            sample=pls,
                            lab_value_name=lab_value_model.name,
                            value=test_value,
                            unit=lab_value_model.default_unit
                        )
                        plv.save()
                        self.assertTrue(requirement.evaluate(plv, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PLV {plv} with value {test_value} (threshold {threshold}).")

                    self.assertTrue(requirement.evaluate(patient, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for Patient with value {test_value}.")
                    self.assertTrue(requirement.evaluate(pls, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PatientLabSample with value {test_value}.")

    def test_lab_value_latest_greater_than_value_requirements_patient(self):
        """
        Verifies that requirements with the 'lab_latest_numeric_greater_than_value' operator are fulfilled for patients of all genders when lab values exceed the specified threshold.
        
        For each patient gender, creates lab samples and lab values just above the requirement's numeric threshold, then asserts that the requirement is fulfilled for the lab value, patient, and lab sample.
        """
        operator_name = "lab_latest_numeric_greater_than_value"
        requirements_for_operator = self.requirements_map.get(operator_name)
        self.assertTrue(requirements_for_operator, f"No requirements found for operator '{operator_name}'. Check fixtures.")

        for gender_name, patient in self.patients.items():
            with self.subTest(gender=gender_name, patient_id=patient.pk):
                for requirement in requirements_for_operator:
                    self.assertIsNotNone(requirement.numeric_value, f"Requirement '{requirement.name}' for '{operator_name}' must have a numeric_value defined.")
                    threshold = requirement.numeric_value
                    test_value = threshold + (1 if threshold > -0.1 else 0.01) 

                    lab_values_linked = requirement.links.lab_values
                    self.assertTrue(lab_values_linked, f"Requirement '{requirement.name}' for operator '{operator_name}' has no linked lab_values.")
                    pls = patient.create_lab_sample()
                    pls.save()

                    for lab_value_model in lab_values_linked:
                        plv = PatientLabValue.create_lab_value_by_sample(
                            sample=pls,
                            lab_value_name=lab_value_model.name,
                            value=test_value,
                            unit=lab_value_model.default_unit
                        )
                        plv.save()
                        self.assertTrue(requirement.evaluate(plv, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PLV {plv} with value {test_value} (threshold {threshold}).")

                    self.assertTrue(requirement.evaluate(patient, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for Patient with value {test_value}.")
                    self.assertTrue(requirement.evaluate(pls, mode="loose"), f"Requirement '{requirement.name}' (operator: {operator_name}, Gender: {gender_name}) not fulfilled for PatientLabSample with value {test_value}.")

# Add similar modifications for other test methods that involve patient gender:
# - test_lab_value_latest_increased_factor_in_timeframe_requirements_patient
# - test_lab_value_latest_decreased_factor_in_timeframe_requirements_patient
# - test_lab_value_latest_normal_in_timeframe_requirements_patient
# - test_lab_value_latest_lower_than_value_in_timeframe_requirements_patient
# - test_lab_value_latest_greater_than_value_in_timeframe_requirements_patient
# ... and any other relevant tests.