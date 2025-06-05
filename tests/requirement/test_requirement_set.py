import logging
from django.test import TestCase
from django.utils import timezone
import datetime

from endoreg_db.models import (
    RequirementSet,
    RequirementSetType,
    LabValue,
    PatientLabValue,
    Unit,
    # GenderChoice,
    # Requirement # Added for direct requirement checks if needed
)
from ..helpers.data_loader import load_data
from ..helpers.default_objects import generate_patient, generate_gender

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RequirementSetEvaluationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        """
        Sets up initial test data for all tests in the class.
        
        Loads fixture data and ensures required units, lab value definitions with normal ranges, and gender objects exist for use in requirement set evaluation tests.
        """
        load_data() # Load all YAML data including RequirementSets, Requirements, LabValues etc.

        # Ensure necessary Units exist
        cls.g_dl, _ = Unit.objects.get_or_create(name="gram per deciliter", defaults={"name": "gram per deciliter"})
        cls.g_l, _ = Unit.objects.get_or_create(name="gram per liter", defaults={"name": "gram per liter"}) # For leukocytes
        cls.umol_l, _ = Unit.objects.get_or_create(name="micromole per liter", defaults={"name": "µmol/L"})
        cls.ratio_unit, _ = Unit.objects.get_or_create(name="ratio", defaults={"name": "ratio"}) # For INR
        cls.percent_unit, _ = Unit.objects.get_or_create(name="percent", defaults={"name": "percent"})


        # Ensure LabValue definitions exist with normal ranges for the "basic_lab_values_normal" set
        # Requirement "lab_value_hb_normal" likely links to LabValue "hemoglobin"
        # Requirement "lab_value_leukocytes_normal" links to LabValue "white_blood_cells"
        # Requirement "lab_value_platelets_normal" links to LabValue "platelets"
        # Requirement "lab_value_creatinine_normal" links to LabValue "creatinine"
        # Requirement "lab_value_inr_normal" links to LabValue "international_normalized_ratio"

        cls.hb, _ = LabValue.objects.get_or_create(
            name="hemoglobin",
            defaults={
                "name_de": "Hämoglobin", "name_en": "Hemoglobin", "value_type": "numeric",
                "numeric_normal_min": 12.0, "numeric_normal_max": 16.0, "unit": cls.g_dl
            }
        )
        cls.wbc, _ = LabValue.objects.get_or_create(
            name="white_blood_cells",
            defaults={
                "name_de": "Leukozyten", "name_en": "White Blood Cells", "value_type": "numeric",
                "numeric_normal_min": 4.0, "numeric_normal_max": 10.0, "unit": cls.g_l # Assuming g/L for WBC
            }
        )
        cls.platelets, _ = LabValue.objects.get_or_create(
            name="platelets",
            defaults={
                "name_de": "Thrombozyten", "name_en": "Platelets", "value_type": "numeric",
                "numeric_normal_min": 150.0, "numeric_normal_max": 400.0, "unit": cls.g_l # Assuming g/L for Platelets
            }
        )
        cls.creatinine, _ = LabValue.objects.get_or_create(
            name="creatinine",
            defaults={
                "name_de": "Kreatinin", "name_en": "Creatinine", "value_type": "numeric",
                "numeric_normal_min": 60.0, "numeric_normal_max": 120.0, "unit": cls.umol_l
            }
        )
        cls.inr, _ = LabValue.objects.get_or_create(
            name="international_normalized_ratio",
            defaults={
                "name_de": "INR", "name_en": "INR", "value_type": "numeric",
                "numeric_normal_min": 0.8, "numeric_normal_max": 1.2, "unit": cls.ratio_unit
            }
        )
        
        # Gender definitions for patient_gender_generic set
        cls.male_gender = generate_gender("male")
        cls.female_gender = generate_gender("female")


    def _create_patient_lab_value(self, patient, lab_value_def, value, days_ago=1):
        """
        Creates a PatientLabValue record for a patient with a specified lab value, numeric result, and timestamp offset by a given number of days.
        
        Args:
            patient: The patient instance for whom the lab value is recorded.
            lab_value_def: The LabValue definition associated with the measurement.
            value: The numeric result of the lab value.
            days_ago: Number of days before the current time to set as the measurement timestamp (default is 1).
        
        Returns:
            The created PatientLabValue instance.
        """
        return PatientLabValue.objects.create(
            patient=patient,
            lab_value=lab_value_def,
            value=value,
            datetime=timezone.now() - datetime.timedelta(days=days_ago)
        )

    def test_basic_lab_values_normal_set_all_normal(self):
        """
        Verifies that the "basic_lab_values_normal" RequirementSet evaluates to True when all associated lab values for a patient are within normal ranges.
        """
        patient = generate_patient()
        patient.save()

        # use LabValue.get_normal_value() to get the normal values for each lab value
        _value_hb = self.hb.get_normal_value(patient=patient)  # Assuming this returns a normal value for hemoglobin
        _value_wbc = self.wbc.get_normal_value(patient=patient)
        _value_platelets = self.platelets.get_normal_value(patient=patient)
        _value_creatinine = self.creatinine.get_normal_value(patient=patient)
        _value_inr = self.inr.get_normal_value(patient=patient)


        self._create_patient_lab_value(patient, self.hb, _value_hb, days_ago=0)       # Normal
        self._create_patient_lab_value(patient, self.wbc, _value_wbc, days_ago=0)       # Normal
        self._create_patient_lab_value(patient, self.platelets, _value_platelets, days_ago=0) # Normal
        self._create_patient_lab_value(patient, self.creatinine, _value_creatinine, days_ago=0) # Normal
        self._create_patient_lab_value(patient, self.inr, _value_inr, days_ago=0)        # Normal

        req_set = RequirementSet.objects.get(name="basic_lab_values_normal")
        self.assertEqual(req_set.requirement_set_type.name, "all")
        
        is_fulfilled = req_set.evaluate(patient)
        self.assertTrue(is_fulfilled, "Set 'basic_lab_values_normal' should be true when all labs are normal.")

    def test_basic_lab_values_normal_set_one_abnormal(self):
        """
        Verifies that the "basic_lab_values_normal" requirement set evaluates to False when one required lab value is abnormal.
        
        This test creates a patient with all required lab values in the normal range except for hemoglobin, which is set to an abnormal (low) value. It asserts that the requirement set of type "all" is not fulfilled when any single lab value requirement fails.
        """
        patient = generate_patient()
        patient.save()

        self._create_patient_lab_value(patient, self.hb, 10.0)       # Abnormal (low)
        self._create_patient_lab_value(patient, self.wbc, 7.0)       # Normal
        self._create_patient_lab_value(patient, self.platelets, 250.0) # Normal
        self._create_patient_lab_value(patient, self.creatinine, 90.0) # Normal
        self._create_patient_lab_value(patient, self.inr, 1.0)        # Normal

        req_set = RequirementSet.objects.get(name="basic_lab_values_normal")
        self.assertEqual(req_set.requirement_set_type.name, "all")

        is_fulfilled = req_set.evaluate(patient)
        self.assertFalse(is_fulfilled, "Set 'basic_lab_values_normal' should be false when one lab is abnormal.")

    def test_patient_gender_generic_set_male(self):
        """
        Tests that the "patient_gender_generic" requirement set evaluates to True for a male patient.
        
        Creates a male patient and verifies that the requirement set of type "any" is fulfilled when evaluated against this patient.
        """
        patient = generate_patient(gender=self.male_gender) # generate_patient needs to support gender
        patient.save()
        
        # Verify that the requirements "patient_gender_is_male" and "patient_gender_is_female" exist
        # and are configured to check patient.gender
        # This test assumes these requirements use an operator that checks patient.gender against a value.
        # For example, "patient_gender_is_male" might check patient.gender == "male_gender_natural_key"

        req_set = RequirementSet.objects.get(name="patient_gender_generic")
        self.assertEqual(req_set.requirement_set_type.name, "any")
        
        is_fulfilled = req_set.evaluate(patient)
        self.assertTrue(is_fulfilled, "Set 'patient_gender_generic' should be true for male patient.")

    def test_patient_gender_generic_set_female(self):
        """
        Verifies that the "patient_gender_generic" requirement set evaluates to True for a female patient.
        
        Creates a female patient and checks that the requirement set of type "any" is fulfilled when evaluated against this patient.
        """
        patient = generate_patient(gender=self.female_gender)
        patient.save()

        req_set = RequirementSet.objects.get(name="patient_gender_generic")
        self.assertEqual(req_set.requirement_set_type.name, "any")

        is_fulfilled = req_set.evaluate(patient)
        self.assertTrue(is_fulfilled, "Set 'patient_gender_generic' should be true for female patient.")
    
    def test_patient_gender_generic_set_unknown_gender(self):
        """
        Tests that the "patient_gender_generic" requirement set evaluates to False for a patient with an unknown gender.
        
        This verifies that when a requirement set of type "any" contains only male and female gender requirements, a patient whose gender is neither male nor female does not fulfill the set.
        """
        other_gender = generate_gender(name="unknown")
        patient = generate_patient(gender=other_gender)
        patient.save()

        req_set = RequirementSet.objects.get(name="patient_gender_generic")
        self.assertEqual(req_set.requirement_set_type.name, "any")

        # This depends on how "patient_gender_is_male/female" requirements are implemented.
        # If they strictly check for "male" or "female" and the patient's gender is "other",
        # both should be false, and thus 'any' would be false.
        is_fulfilled = req_set.evaluate(patient)

        #TODO implement the Gender Requirements        
        # We need to know how `patient_gender_is_male` and `patient_gender_is_female` requirements work.
        # Let's assume they are defined to check patient.gender against specific GenderChoice instances.
        # If patient.gender is 'other', both specific checks should fail.
        # req_male = Requirement.objects.get(name="patient_gender_is_male")
        # req_female = Requirement.objects.get(name="patient_gender_is_female")
        # self.assertFalse(req_male.evaluate(patient), "Male requirement should be false for 'other' gender")
        # self.assertFalse(req_female.evaluate(patient), "Female requirement should be false for 'other' gender")

        self.assertFalse(is_fulfilled, "Set 'patient_gender_generic' should be false for 'other' gender if only male/female reqs exist.")

    def test_empty_requirement_set_all_type(self):
        """
        Tests that an empty 'all' type RequirementSet evaluates to True for any patient.
        
        Creates a RequirementSet of type 'all' with no requirements or linked sets, evaluates it against a patient, and asserts that the result is True.
        """
        all_type = RequirementSetType.objects.get(name="all")
        empty_set = RequirementSet.objects.create(name="empty_all_set_test", requirement_set_type=all_type)
        patient = generate_patient()
        patient.save()
        self.assertTrue(empty_set.evaluate(patient), "'all' type set with no requirements should be True.")
        empty_set.delete()


    def test_empty_requirement_set_any_type(self):
        """
        Tests that an empty RequirementSet of type 'any' evaluates to False.
        
        Creates an 'any' type RequirementSet with no requirements or linked sets, evaluates it against a patient, and asserts that the result is False.
        """
        any_type = RequirementSetType.objects.get(name="any")
        empty_set = RequirementSet.objects.create(name="empty_any_set_test", requirement_set_type=any_type)
        patient = generate_patient()
        patient.save()
        self.assertFalse(empty_set.evaluate(patient), "'any' type set with no requirements should be False.")
        empty_set.delete()

    def test_empty_requirement_set_none_type(self):
        """
        Tests that an empty 'none' type RequirementSet with no requirements or linked sets evaluates to True for a patient.
        """
        none_type = RequirementSetType.objects.get(name="none")
        empty_set = RequirementSet.objects.create(name="empty_none_set_test", requirement_set_type=none_type)
        patient = generate_patient()
        patient.save()
        self.assertTrue(empty_set.evaluate(patient), "'none' type set with no requirements should be True.")
        empty_set.delete()

