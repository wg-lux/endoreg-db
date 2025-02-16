'''Model for numeric value distribution'''

from django.db import models
import numpy as np
from .base_value_distribution import BaseValueDistribution
from scipy.stats import skewnorm


class NumericValueDistributionManager(models.Manager):
    '''Object manager for NumericValueDistribution'''
    def get_by_natural_key(self, name):
        '''Retrieve a NumericValueDistribution by its natural key.'''
        return self.get(name=name)

class NumericValueDistribution(BaseValueDistribution):
    """
    Numeric value distribution model.
    Supports uniform, normal, and skewed normal distributions with hard limits.
    """
    objects = NumericValueDistributionManager()
    DISTRIBUTION_CHOICES = [
        ('uniform', 'Uniform'),
        ('normal', 'Normal'),
        ('skewed_normal', 'Skewed Normal'),
    ]

    distribution_type = models.CharField(max_length=20, choices=DISTRIBUTION_CHOICES)
    min_descriptor = models.CharField(
        max_length=20
    )

    max_descriptor = models.CharField(
        max_length=20
    )

    def generate_value(self, lab_value, patient):
        '''Generate a value based on the distribution rules.'''
        #FIXME
        from endoreg_db.models import LabValue, Patient
        assert isinstance(patient, Patient)
        assert isinstance(lab_value, LabValue)
        default_normal_range_dict = lab_value.get_normal_range(patient.age(), patient.gender)
        assert isinstance(default_normal_range_dict, dict)
        if self.distribution_type == 'uniform':
            assert self.min_descriptor and self.max_descriptor
            assert "min" in default_normal_range_dict and "max" in default_normal_range_dict
            value = self._generate_value_uniform(default_normal_range_dict)
            
            return value
        
        elif self.distribution_type == 'normal':
            value = np.random.normal(self.mean, self.std_dev)
            return np.clip(value, self.min_value, self.max_value)
        elif self.distribution_type == 'skewed_normal':
            value = skewnorm.rvs(a=self.skewness, loc=self.mean, scale=self.std_dev)
            return np.clip(value, self.min_value, self.max_value)
        else:
            raise ValueError("Unsupported distribution type")

    def parse_value_descriptor(self, value_descriptor:str):
        '''Parse the value descriptor string into a dict with a lambda function.'''
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

    def _generate_value_uniform(self, default_normal_range_dict:dict):
        value_function_dict = self.parse_value_descriptor(
            self.min_descriptor
        )

        _ = self.parse_value_descriptor(
            self.max_descriptor
        )

        value_function_dict.update(_)

        result_dict = {
            key: value_function(default_normal_range_dict[key]) 
            for key, value_function in value_function_dict.items()
        }

        # generate value
        return np.random.uniform(result_dict["min"], result_dict["max"])

    