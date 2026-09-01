from __future__ import annotations
from typing import Unpack

import numpy as np
from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.anonymization import normalize_categorical_distribution

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

    def clean(self) -> None:
        super().clean()
        self.categories = normalize_categorical_distribution(self.categories)

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(**kwargs)

    def generate_value(self, *args: object, **kwargs: object) -> object:
        categories, probabilities = zip(*self.categories.items())
        return np.random.choice(categories, p=probabilities)
