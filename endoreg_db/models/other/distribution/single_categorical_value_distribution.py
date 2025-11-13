import numpy as np
from django.db import models

from .base_value_distribution import BaseValueDistribution


class SingleCategoricalValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class SingleCategoricalValueDistribution(BaseValueDistribution):
    """
    Single categorical value distribution model.
    Assigns a single value based on specified probabilities.
    """

    objects = SingleCategoricalValueDistributionManager()
    categories = models.JSONField()  # { "category": "probability", ... }

    def generate_value(self):
        categories, probabilities = zip(*self.categories.items())
        return np.random.choice(categories, p=probabilities)
