from __future__ import annotations
from abc import abstractmethod

from django.db import models


class BaseValueDistribution(models.Model):
    """
    Abstract base class for value distributions.
    """

    name: models.CharField[str] = models.CharField(max_length=100)

    class Meta:
        abstract = True

    @abstractmethod
    def generate_value(self, *args: object, **kwargs: object) -> object:
        """
        Generate a value based on the distribution rules.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement this method")

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)
