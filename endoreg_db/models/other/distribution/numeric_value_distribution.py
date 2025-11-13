"""Model for numeric value distribution"""

import numpy as np
from django.db import models
from scipy.stats import skewnorm
from typing import TYPE_CHECKING

from .base_value_distribution import BaseValueDistribution

if TYPE_CHECKING:
    from endoreg_db.models import LabValue, Patient


class NumericValueDistributionManager(models.Manager):
    """Object manager for NumericValueDistribution"""

    def get_by_natural_key(self, name):
        """Retrieve a NumericValueDistribution by its natural key."""
        return self.get(name=name)


class NumericValueDistribution(BaseValueDistribution):
    """
    Numeric value distribution model.
    Supports uniform, normal, and skewed normal distributions with hard limits.
    """

    objects = NumericValueDistributionManager()
    DISTRIBUTION_CHOICES = [
        ("uniform", "Uniform"),
        ("normal", "Normal"),
        ("skewed_normal", "Skewed Normal"),
    ]

    distribution_type = models.CharField(max_length=20, choices=DISTRIBUTION_CHOICES)
    min_descriptor = models.CharField(max_length=20)
    max_descriptor = models.CharField(max_length=20)
    min_value = models.FloatField(blank=True, null=True, help_text="Lower hard limit for generated values")
    max_value = models.FloatField(blank=True, null=True, help_text="Upper hard limit for generated values")
    mean = models.FloatField(blank=True, null=True, help_text="Mean used for normal or skewed normal distributions")
    std_dev = models.FloatField(blank=True, null=True, help_text="Standard deviation for bell-shaped distributions")
    skewness = models.FloatField(blank=True, null=True, help_text="Shape parameter for skewed normal distributions")

    @property
    def min_value_safe(self):
        if self.min_value is None:
            raise ValueError("min_value is not set")
        return self.min_value

    @property
    def max_value_safe(self):
        if self.max_value is None:
            raise ValueError("max_value is not set")
        return self.max_value

    @property
    def mean_safe(self):
        if self.mean is None:
            raise ValueError("mean is not set")
        return self.mean

    @property
    def std_dev_safe(self):
        if self.std_dev is None:
            raise ValueError("std_dev is not set")
        return self.std_dev

    @property
    def skewness_safe(self):
        if self.skewness is None:
            raise ValueError("skewness is not set")
        return self.skewness

    def generate_value(self, lab_value: "LabValue", patient: "Patient"):
        """Generate a value based on the distribution rules."""

        default_normal_range_dict = lab_value.get_normal_range(patient.age_safe, patient.gender)
        assert isinstance(default_normal_range_dict, dict)

        if self.distribution_type == "uniform":
            assert self.min_descriptor and self.max_descriptor

            min_key_needed = self.min_descriptor.split("_")[0]
            max_key_needed = self.max_descriptor.split("_")[0]

            min_val_from_range = default_normal_range_dict.get(min_key_needed)
            max_val_from_range = default_normal_range_dict.get(max_key_needed)

            if min_val_from_range is None:
                raise ValueError(
                    f"Cannot generate value for LabValue '{lab_value.name}' using distribution "
                    f"'{getattr(self, 'name', self.pk)}'. "
                    f"Required normal range component '{min_key_needed}' derived from min_descriptor "
                    f"'{self.min_descriptor}' is None. "
                    f"Patient context: age={patient.age()}, gender='{patient.gender.name if patient.gender else None}'. "
                    f"LabValue '{lab_value.name}' is gender-dependent: {lab_value.normal_range_gender_dependent}. "
                    f"Check LabValue's default_normal_range definition for this patient context."
                )

            if max_val_from_range is None:
                raise ValueError(
                    f"Cannot generate value for LabValue '{lab_value.name}' using distribution "
                    f"'{getattr(self, 'name', self.pk)}'. "
                    f"Required normal range component '{max_key_needed}' derived from max_descriptor "
                    f"'{self.max_descriptor}' is None. "
                    f"Patient context: age={patient.age()}, gender='{patient.gender.name if patient.gender else None}'. "
                    f"LabValue '{lab_value.name}' is gender-dependent: {lab_value.normal_range_gender_dependent}. "
                    f"Check LabValue's default_normal_range definition for this patient context."
                )

            value = self._generate_value_uniform(default_normal_range_dict)

            return value

        elif self.distribution_type == "normal":
            self._validate_normal_parameters()
            assert self.mean is not None
            assert self.std_dev is not None
            value = np.random.normal(self.mean, self.std_dev)
            return self._clip_to_bounds(value)
        elif self.distribution_type == "skewed_normal":
            self._validate_skewed_normal_parameters()
            assert self.mean is not None
            assert self.std_dev is not None
            assert self.skewness is not None
            value = skewnorm.rvs(a=self.skewness, loc=self.mean, scale=self.std_dev)
            return self._clip_to_bounds(value)
        else:
            raise ValueError("Unsupported distribution type")

    def parse_value_descriptor(self, value_descriptor: str):
        """Parse the value descriptor string into a dict with a lambda function."""
        # strings of shape f"{value_key}_{operator}_{value}"
        # extract value_key, operator, value
        value_key, operator, value = value_descriptor.split("_")
        value = float(value)

        operator_functions = {
            "+": lambda x: x + value,
            "-": lambda x: x - value,
            "x": lambda x: x * value,
            "/": lambda x: x / value,
        }

        return {value_key: operator_functions[operator]}

        # create dict with {value_key: lambda x: x operator value}

    def _generate_value_uniform(self, default_normal_range_dict: dict):
        value_function_dict = self.parse_value_descriptor(self.min_descriptor)
        value_function_dict.update(self.parse_value_descriptor(self.max_descriptor))

        result_dict = {key: value_function(default_normal_range_dict[key]) for key, value_function in value_function_dict.items()}

        # generate value
        return float(np.random.uniform(result_dict["min"], result_dict["max"]))

    @property
    def stddev(self):
        """Alias to std_dev for backwards compatibility."""
        return self.std_dev

    @stddev.setter
    def stddev(self, value):
        self.std_dev = value

    def _clip_to_bounds(self, value: float) -> float:
        """Clip the provided value to the configured hard bounds if available."""
        lower = self.min_value
        upper = self.max_value

        if lower is not None and upper is not None:
            return float(np.clip(value, lower, upper))
        if lower is not None:
            return float(max(value, lower))
        if upper is not None:
            return float(min(value, upper))
        return float(value)

    def _validate_normal_parameters(self) -> None:
        if self.mean is None or self.std_dev is None:
            raise ValueError(f"Normal distribution '{getattr(self, 'name', self.pk)}' requires both mean and std_dev.")

    def _validate_skewed_normal_parameters(self) -> None:
        if self.mean is None or self.std_dev is None or self.skewness is None:
            raise ValueError(f"Skewed normal distribution '{getattr(self, 'name', self.pk)}' requires mean, std_dev, and skewness.")
