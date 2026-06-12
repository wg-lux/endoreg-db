import numpy as np
from django.db import models

from .base_value_distribution import BaseValueDistribution


class MultipleCategoricalValueDistributionManager(
    models.Manager["MultipleCategoricalValueDistribution"]
):
    def get_by_natural_key(self, name: str) -> "MultipleCategoricalValueDistribution":
        return self.get(name=name)


class MultipleCategoricalValueDistribution(BaseValueDistribution):
    """
    Multiple categorical value distribution model.
    Assigns a specific number or varying number of values based on probabilities.
    """

    objects = MultipleCategoricalValueDistributionManager()
    categories: models.JSONField[dict[str, float], dict[str, float]] = models.JSONField()
    min_count: models.IntegerField[int, int] = models.IntegerField()
    max_count: models.IntegerField[int, int] = models.IntegerField()
    count_distribution_type: models.CharField[str, str] = models.CharField(
        max_length=20, choices=[("uniform", "Uniform"), ("normal", "Normal")]
    )
    count_mean: models.FloatField[float | None, float | None] = models.FloatField(
        null=True, blank=True
    )
    count_std_dev: models.FloatField[float | None, float | None] = models.FloatField(
        null=True, blank=True
    )

    @property
    def count_mean_safe(self):
        if self.count_mean is None:
            raise ValueError("count_mean is not set")
        return self.count_mean

    @property
    def count_std_dev_safe(self):
        if self.count_std_dev is None:
            raise ValueError("count_std_dev is not set")
        return self.count_std_dev

    def generate_value(self, *args: object, **kwargs: object) -> object:
        if self.count_distribution_type == "uniform":
            count = np.random.randint(self.min_count, self.max_count + 1)
        elif self.count_distribution_type == "normal":
            count = int(np.random.normal(self.count_mean_safe, self.count_std_dev_safe))
            count = np.clip(count, self.min_count, self.max_count)
        else:
            raise ValueError("Unsupported count distribution type")

        categories, probabilities = zip(*self.categories.items())
        return list(np.random.choice(categories, size=count, p=probabilities))
