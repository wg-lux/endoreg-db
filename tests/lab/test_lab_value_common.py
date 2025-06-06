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
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.administration.person.patient import Patient
from endoreg_db.models.other.distribution import NumericValueDistribution # For type hinting/mocking
from unittest.mock import MagicMock, patch, PropertyMock


logger = getLogger(__name__)
logger.setLevel(logging.INFO) # Changed to INFO for more verbose logging during test development


class CommonLabValuesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        """
        Initializes test data for the CommonLabValuesTest class.
        
        Creates and retrieves LabValue and Gender instances with various normal range configurations
        to support different test scenarios. This includes lab values with no range, min-only, max-only,
        zero bounds, and reference values for distribution-based tests.
        """
        load_data()
        
        cls.hb = LabValue.objects.get(name="hemoglobin")
        cls.platelets = LabValue.objects.get(name="platelets") # Used as lv_with_dist_ref
        
        cls.no_range_lv, _ = LabValue.objects.get_or_create(
            name="no_range_test_lv_common",
            defaults={'name_de': "Kein Bereich LV", 'name_en': "No Range LV"}
        )
        cls.min_only_lv, _ = LabValue.objects.get_or_create(
            name="min_only_test_lv_common",
            defaults={
                'name_de': "Nur Min LV", 
                'name_en': "Min Only LV",
                'default_normal_range': {"min": 10}
            }
        )
        cls.max_only_lv, _ = LabValue.objects.get_or_create(
            name="max_only_test_lv_common",
            defaults={
                'name_de': "Nur Max LV", 
                'name_en': "Max Only LV",
                'default_normal_range': {"max": 100}
            }
        )
        cls.zero_max_lv, _ = LabValue.objects.get_or_create(
            name="zero_max_lv_common",
            defaults={'default_normal_range': {"min": -10, "max": 0}}
        )
        cls.zero_min_lv, _ = LabValue.objects.get_or_create(
            name="zero_min_lv_common",
            defaults={'default_normal_range': {"min": 0, "max": 10}}
        )

        cls.male_gender, _ = Gender.objects.get_or_create(name="male", defaults={'name_de': "männlich", 'name_en': "male"})
        cls.female_gender, _ = Gender.objects.get_or_create(name="female", defaults={'name_de': "weiblich", 'name_en': "female"})
        cls.unknown_gender, _ = Gender.objects.get_or_create(name="unknown", defaults={'name_de': "unbekannt", 'name_en': "unknown"})

        cls.lv_with_dist_ref = cls.platelets

        cls.lv_min_only_dist, _ = LabValue.objects.get_or_create(
            name="lv_min_only_dist_common",
            defaults={'default_normal_range': {"min": 10}}
        )
        cls.lv_max_only_dist, _ = LabValue.objects.get_or_create(
            name="lv_max_only_dist_common",
            defaults={'default_normal_range': {"max": 100}}
        )
        cls.lv_no_range_dist, _ = LabValue.objects.get_or_create(
            name="lv_no_range_dist_common"
        )

    def setUp(self):
        """
        Creates mock male and female patient objects with predefined age and gender for use in test cases.
        """
        self.mock_male_patient = MagicMock(spec=Patient)
        self.mock_male_patient.age.return_value = 30
        self.mock_male_patient.gender = self.male_gender

        self.mock_female_patient = MagicMock(spec=Patient)
        self.mock_female_patient.age.return_value = 30
        self.mock_female_patient.gender = self.female_gender

    def test_common_lab_values(self):
        """
        Tests that LabValue.get_common_lab_values() returns a CommonLabValues instance.
        """
        common_lab_values = LabValue.get_common_lab_values()
        self.assertIsInstance(common_lab_values, CommonLabValues, "Expected CommonLabValues instance.")

    def test_get_normal_range_gender_dependent(self):
        # Hemoglobin: Male: 14-18, Female: 12-16
        """
        Tests that gender-dependent normal ranges are correctly returned for hemoglobin.
        
        Verifies that the correct normal range is provided for male and female genders, and that appropriate warnings are issued and fallback behavior occurs when gender is missing or unknown.
        """
        normal_range_male = self.hb.get_normal_range(gender=self.male_gender)
        self.assertEqual(normal_range_male, {"min": 14, "max": 18})

        normal_range_female = self.hb.get_normal_range(gender=self.female_gender)
        self.assertEqual(normal_range_female, {"min": 12, "max": 16})

        with self.assertWarnsRegex(UserWarning, "Gender not provided.*Defaulting to 'male' range"):
            normal_range_none = self.hb.get_normal_range()
            self.assertEqual(normal_range_none, {"min": 14, "max": 18})
        
        with self.assertWarnsRegex(UserWarning, "Normal range for gender 'unknown' not found.*Defaulting to 'male' range"):
            normal_range_unknown = self.hb.get_normal_range(gender=self.unknown_gender)
            self.assertEqual(normal_range_unknown, {"min": 14, "max": 18})

    def test_get_normal_range_non_gender_dependent(self):
        # Platelets: 150-350
        """
        Tests that the normal range for a non-gender-dependent lab value is consistent regardless of gender.
        
        Verifies that calling `get_normal_range` on the platelets lab value returns the same range whether or not a gender is specified.
        """
        normal_range = self.platelets.get_normal_range()
        self.assertEqual(normal_range, {"min": 150, "max": 350})

        normal_range_male = self.platelets.get_normal_range(gender=self.male_gender)
        self.assertEqual(normal_range_male, {"min": 150, "max": 350})

    # def test_get_normal_range_no_default_range(self):
    #     with self.assertWarnsRegex(UserWarning, "Could not determine a 'min' normal range"):
    #         normal_range = self.no_range_lv.get_normal_range()
    #         self.assertIsNone(normal_range.get("min"))
    #         self.assertIsNone(normal_range.get("max"))
            
    def test_get_normal_range_min_only(self):
        """
        Tests that a lab value with only a minimum normal range returns the correct min value and no max value.
        """
        normal_range = self.min_only_lv.get_normal_range()
        self.assertEqual(normal_range.get("min"), 10)
        self.assertIsNone(normal_range.get("max"))

    def test_get_normal_range_max_only(self):
        """
        Tests that get_normal_range returns only the maximum value when the lab value has a max-only normal range.
        
        Verifies that the minimum is None and the maximum is correctly set.
        """
        normal_range = self.max_only_lv.get_normal_range()
        self.assertIsNone(normal_range.get("min"))
        self.assertEqual(normal_range.get("max"), 100)

    # Tests for get_increased_value
    def test_get_increased_value_no_distribution_with_upper_bound(self):
        """
        Tests that get_increased_value returns the upper bound plus adjustment factor when no distribution is present and an upper bound exists.
        """
        original = self.platelets.default_numerical_value_distribution
        self.platelets.default_numerical_value_distribution = None
        try:
            increased_value = self.platelets.get_increased_value()
            self.assertEqual(increased_value, 350 + (350 * self.platelets.bound_adjustment_factor))
        finally:
            self.platelets.default_numerical_value_distribution = original

    def test_get_increased_value_no_distribution_no_upper_bound(self):
        """
        Tests that get_increased_value returns None and issues a warning when called on a lab value with no distribution and no upper normal range.
        """
        original = self.min_only_lv.default_numerical_value_distribution
        self.min_only_lv.default_numerical_value_distribution = None
        try:
            with self.assertWarnsRegex(UserWarning, "Cannot determine an increased value.*without.*an upper normal range"):
                increased_value = self.min_only_lv.get_increased_value()
                self.assertIsNone(increased_value)
        finally:
            self.min_only_lv.default_numerical_value_distribution = original

    def test_get_increased_value_upper_bound_zero(self):
        """
        Tests that get_increased_value returns 1 when the upper bound is zero and no distribution is present.
        """
        original = self.zero_max_lv.default_numerical_value_distribution
        self.zero_max_lv.default_numerical_value_distribution = None
        try:
            increased_value = self.zero_max_lv.get_increased_value()
            self.assertEqual(increased_value, 1) # 0 + 1
        finally:
            self.zero_max_lv.default_numerical_value_distribution = original

    # Tests for get_normal_value
    def test_get_normal_value_no_distribution_min_only(self):
        """
        Tests that get_normal_value returns the minimum value when no distribution exists and only a minimum bound is set.
        """
        original = self.min_only_lv.default_numerical_value_distribution
        self.min_only_lv.default_numerical_value_distribution = None
        try:
            normal_value = self.min_only_lv.get_normal_value()
            self.assertEqual(normal_value, 10)
        finally:
            self.min_only_lv.default_numerical_value_distribution = original

    def test_get_normal_value_no_distribution_max_only(self):
        """
        Tests that get_normal_value returns the maximum value when only a maximum bound is defined and no distribution is present.
        """
        original = self.max_only_lv.default_numerical_value_distribution
        self.max_only_lv.default_numerical_value_distribution = None
        try:
            normal_value = self.max_only_lv.get_normal_value()
            self.assertEqual(normal_value, 100)
        finally:
            self.max_only_lv.default_numerical_value_distribution = original

    def test_get_normal_value_no_distribution_no_bounds(self):
        """
        Tests that get_normal_value returns None and issues a warning when neither a numerical distribution nor normal range bounds are available.
        """
        original = self.no_range_lv.default_numerical_value_distribution
        self.no_range_lv.default_numerical_value_distribution = None
        try:
            with self.assertWarnsRegex(UserWarning, r"Cannot determine a normal value.*without a numerical distribution or a normal range"):
                normal_value = self.no_range_lv.get_normal_value()
                self.assertIsNone(normal_value)
        finally:
            self.no_range_lv.default_numerical_value_distribution = original

    # Tests for get_decreased_value
    def test_get_decreased_value_no_distribution_with_lower_bound(self):
        """
        Tests that get_decreased_value returns the lower bound minus the adjustment factor when no numerical value distribution is present and a lower bound exists.
        """
        original = self.platelets.default_numerical_value_distribution
        self.platelets.default_numerical_value_distribution = None
        try:
            decreased_value = self.platelets.get_decreased_value()
            self.assertEqual(decreased_value, 150 - (150 * self.platelets.bound_adjustment_factor))
        finally:
            self.platelets.default_numerical_value_distribution = original

    def test_get_decreased_value_no_distribution_no_lower_bound(self):
        """
        Tests that get_decreased_value returns None and issues a warning when no distribution or lower normal range bound exists.
        """
        original = self.max_only_lv.default_numerical_value_distribution
        self.max_only_lv.default_numerical_value_distribution = None
        try:
            with self.assertWarnsRegex(UserWarning, "Cannot determine a decreased value.*without.*a lower normal range"):
                decreased_value = self.max_only_lv.get_decreased_value()
                self.assertIsNone(decreased_value)
        finally:
            self.max_only_lv.default_numerical_value_distribution = original

    def test_get_decreased_value_lower_bound_zero(self):
        """
        Tests that `get_decreased_value` returns -1 when the lower bound is zero and no distribution is present.
        """
        original = self.zero_min_lv.default_numerical_value_distribution
        self.zero_min_lv.default_numerical_value_distribution = None
        try:
            decreased_value = self.zero_min_lv.get_decreased_value()
            self.assertEqual(decreased_value, -1) # 0 - 1
        finally:
            self.zero_min_lv.default_numerical_value_distribution = original

    # Tests with distribution
    def test_get_increased_value_with_distribution_patient(self):
        """
        Tests that get_increased_value returns an appropriately increased lab value using a numerical distribution and patient context.
        
        Verifies that:
        - When the generated value exceeds the upper bound, it is returned directly.
        - When the generated value does not exceed the upper bound after multiple attempts, a fallback calculation is used.
        - For lab values without an upper bound, the method uses a mean plus standard deviation heuristic, with similar retry and fallback logic.
        """
        mock_distribution = MagicMock(spec=NumericValueDistribution)
        # Platelets (lv_with_dist_ref): max 350
        with patch.object(type(self.lv_with_dist_ref), 'default_numerical_value_distribution', new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_distribution
            # Scenario 1: generated value > upper_bound
            mock_distribution.generate_value.return_value = 400
            increased_value = self.lv_with_dist_ref.get_increased_value(patient=self.mock_male_patient)
            self.assertEqual(increased_value, 400)
            mock_distribution.generate_value.assert_called_once_with(lab_value=self.lv_with_dist_ref, patient=self.mock_male_patient)

            # Scenario 2: generated value <= upper_bound (tries 10 times, then fallback to calculation)
            mock_distribution.reset_mock()
            mock_distribution.generate_value.return_value = 300 
            increased_value = self.lv_with_dist_ref.get_increased_value(patient=self.mock_male_patient)
            self.assertEqual(increased_value, 350 + (350 * self.lv_with_dist_ref.bound_adjustment_factor))
            self.assertEqual(mock_distribution.generate_value.call_count, 10)

        # Scenario 3: No upper bound, uses mean + stddev heuristic
        mock_dist_mean_std = MagicMock(spec=NumericValueDistribution)
        mock_dist_mean_std.mean = 20
        mock_dist_mean_std.stddev = 5
        with patch.object(type(self.lv_min_only_dist), 'default_numerical_value_distribution', new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_dist_mean_std
            mock_dist_mean_std.generate_value.return_value = 26 # > mean + stddev (25)
            increased_value = self.lv_min_only_dist.get_increased_value(patient=self.mock_male_patient)
            self.assertEqual(increased_value, 26)
            mock_dist_mean_std.generate_value.assert_called_once_with(lab_value=self.lv_min_only_dist, patient=self.mock_male_patient)

            mock_dist_mean_std.reset_mock()
            mock_dist_mean_std.generate_value.return_value = 24 # < mean + stddev
            increased_value = self.lv_min_only_dist.get_increased_value(patient=self.mock_male_patient)
            self.assertEqual(increased_value, 24) # Fallback to last generated value
            self.assertEqual(mock_dist_mean_std.generate_value.call_count, 10 + 1) 

    def test_get_increased_value_with_distribution_no_patient(self):
        """
        Tests that get_increased_value falls back to a calculated value and issues a warning
        when called without patient context, even if a numerical distribution exists.
        """
        self.assertIsNotNone(self.lv_with_dist_ref.default_numerical_value_distribution, 
                             "Platelets (lv_with_dist_ref) must have a distribution from load_data()")
        with self.assertWarnsRegex(UserWarning, "Cannot use numerical distribution.*without patient context"):
            increased_value = self.lv_with_dist_ref.get_increased_value()
            self.assertEqual(increased_value, 350 + (350 * self.lv_with_dist_ref.bound_adjustment_factor))

    def test_get_normal_value_with_distribution_patient(self):
        """
        Tests that get_normal_value returns appropriate values when a numerical distribution is present and a patient is provided.
        
        Verifies that:
        - If the generated value is within the normal range, it is returned.
        - If the generated value is outside the normal range, the method retries up to 10 times before falling back to the midpoint of the range.
        - If no normal range is defined, the generated value is returned.
        """
        mock_distribution = MagicMock(spec=NumericValueDistribution)
        # Platelets (lv_with_dist_ref): 150-350
        with patch.object(type(self.lv_with_dist_ref), 'default_numerical_value_distribution', new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_distribution
            # Scenario 1: generated value is within normal range
            mock_distribution.generate_value.return_value = 200
            normal_value = self.lv_with_dist_ref.get_normal_value(patient=self.mock_male_patient)
            self.assertEqual(normal_value, 200)
            mock_distribution.generate_value.assert_called_once_with(lab_value=self.lv_with_dist_ref, patient=self.mock_male_patient)

            # Scenario 2: generated value outside range (tries 10 times, then fallback to calculation)
            mock_distribution.reset_mock()
            mock_distribution.generate_value.return_value = 400
            normal_value = self.lv_with_dist_ref.get_normal_value(patient=self.mock_male_patient)
            self.assertEqual(normal_value, (150 + 350) / 2.0)
            self.assertEqual(mock_distribution.generate_value.call_count, 10)

        # Scenario 3: No normal range defined, returns generated value
        mock_no_range_dist = MagicMock(spec=NumericValueDistribution)
        with patch.object(type(self.lv_no_range_dist), 'default_numerical_value_distribution', new_callable=PropertyMock) as mock_prop:
            mock_prop.return_value = mock_no_range_dist
            mock_no_range_dist.generate_value.return_value = 77
            normal_value = self.lv_no_range_dist.get_normal_value(patient=self.mock_male_patient)
            self.assertEqual(normal_value, 77)
            mock_no_range_dist.generate_value.assert_called_once_with(lab_value=self.lv_no_range_dist, patient=self.mock_male_patient)

    def test_get_normal_value_with_distribution_no_patient(self):
        """
        Tests that get_normal_value falls back to the midpoint of the normal range and issues a warning when called without patient context, even if a numerical value distribution exists.
        """
        self.assertIsNotNone(self.lv_with_dist_ref.default_numerical_value_distribution)
        with self.assertWarnsRegex(UserWarning, "Cannot use numerical distribution.*without patient context"):
            normal_value = self.lv_with_dist_ref.get_normal_value()
            self.assertEqual(normal_value, (150 + 350) / 2.0)

    def test_get_decreased_value_with_distribution_patient(self):
        # Get the real distribution object
        """
        Tests decreased value generation for a lab value with a numerical distribution and patient context.
        
        Covers scenarios where the generated value is below the lower bound, above the lower bound (triggering retries and fallback), and when no lower bound exists (using mean minus standard deviation heuristic).
        """
        dist = self.lv_with_dist_ref.default_numerical_value_distribution

        # Scenario 1: generated value < lower_bound
        with patch.object(dist, 'generate_value', return_value=100) as mock_gen:
            decreased_value = self.lv_with_dist_ref.get_decreased_value(patient=self.mock_male_patient)
            self.assertEqual(decreased_value, 100)
            mock_gen.assert_called_once_with(lab_value=self.lv_with_dist_ref, patient=self.mock_male_patient)

        # Scenario 2: generated value >= lower_bound (tries 10 times, then fallback to calculation)
        with patch.object(dist, 'generate_value', return_value=200) as mock_gen:
            decreased_value = self.lv_with_dist_ref.get_decreased_value(patient=self.mock_male_patient)
            self.assertEqual(decreased_value, 150 - (150 * self.lv_with_dist_ref.bound_adjustment_factor))
            self.assertEqual(mock_gen.call_count, 10)

        # Scenario 3: No lower bound, uses mean - stddev heuristic
        mock_dist_mean_std = MagicMock(spec=NumericValueDistribution)
        mock_dist_mean_std.mean = 80
        mock_dist_mean_std.stddev = 10
        with patch.object(
            type(self.lv_max_only_dist),
            'default_numerical_value_distribution',
            new_callable=PropertyMock
        ) as mock_prop:
            mock_prop.return_value = mock_dist_mean_std
            with patch.object(mock_dist_mean_std, 'generate_value', return_value=65) as mock_gen:
                decreased_value = self.lv_max_only_dist.get_decreased_value(patient=self.mock_male_patient)
                self.assertEqual(decreased_value, 65)
                mock_gen.assert_called_once_with(lab_value=self.lv_max_only_dist, patient=self.mock_male_patient)
            with patch.object(mock_dist_mean_std, 'generate_value', return_value=75) as mock_gen:
                decreased_value = self.lv_max_only_dist.get_decreased_value(patient=self.mock_male_patient)
                self.assertEqual(decreased_value, 75) # Fallback to last generated value
                self.assertEqual(mock_gen.call_count, 10 + 1)

    def test_get_decreased_value_with_distribution_no_patient(self):
        """
        Tests that get_decreased_value falls back to a calculated value and issues a warning when called without patient context and a numerical distribution is present.
        """
        self.assertIsNotNone(self.lv_with_dist_ref.default_numerical_value_distribution)
        with self.assertWarnsRegex(UserWarning, "Cannot use numerical distribution.*without patient context"):
            decreased_value = self.lv_with_dist_ref.get_decreased_value()
            self.assertEqual(decreased_value, 150 - (150 * self.lv_with_dist_ref.bound_adjustment_factor))
