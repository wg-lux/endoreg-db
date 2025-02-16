from django.db import models
import numpy as np
from .base_value_distribution import BaseValueDistribution

class MultipleCategoricalValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class MultipleCategoricalValueDistribution(BaseValueDistribution):
    """
    Multiple categorical value distribution model.
    Assigns a specific number or varying number of values based on probabilities.
    """
    objects = MultipleCategoricalValueDistributionManager()
    categories = models.JSONField()  # { "category": "probability", ... }
    min_count = models.IntegerField()
    max_count = models.IntegerField()
    count_distribution_type = models.CharField(max_length=20, choices=[('uniform', 'Uniform'), ('normal', 'Normal')])
    count_mean = models.FloatField(null=True, blank=True)
    count_std_dev = models.FloatField(null=True, blank=True)

    def generate_value(self):
        if self.count_distribution_type == 'uniform':
            count = np.random.randint(self.min_count, self.max_count + 1)
        elif self.count_distribution_type == 'normal':
            count = int(np.random.normal(self.count_mean, self.count_std_dev))
            count = np.clip(count, self.min_count, self.max_count)
        else:
            raise ValueError("Unsupported count distribution type")

        categories, probabilities = zip(*self.categories.items())
        return list(np.random.choice(categories, size=count, p=probabilities))
