from __future__ import annotations
import numpy as np
from django.db import models

from .base_value_distribution import BaseValueDistribution


class SingleCategoricalValueDistributionManager(
    models.Manager["SingleCategoricalValueDistribution"]
):
    def get_by_natural_key(self, name: str) -> "SingleCategoricalValueDistribution":
        return self.get(name=name)


class SingleCategoricalValueDistribution(BaseValueDistribution):
    """
    Single categorical value distribution model.
    Assigns a single value based on specified probabilities.
    """

    objects = SingleCategoricalValueDistributionManager()
    categories: models.JSONField[dict[str, float]] = models.JSONField()

    def generate_value(self, *args: object, **kwargs: object) -> object:
        categories, probabilities = zip(*self.categories.items())
        return np.random.choice(categories, p=probabilities)
