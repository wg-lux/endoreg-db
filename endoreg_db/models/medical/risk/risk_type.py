from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

# Deorecate ?


class RiskType(models.Model):
    """
    A class representing a risk type.

    Attributes:
        name (str): The name of the risk type.
        description (str): A description of the risk type.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    objects = NameNaturalKeyManager["RiskType"]()

    if TYPE_CHECKING:
        from endoreg_db.models import Risk

        risks: models.QuerySet[Risk]

    def natural_key(self) -> tuple[str]:
        """
        Return the natural key for this risk type.

        This method returns a tuple containing only the risk type's unique name, which is used
        to identify the instance naturally.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return the risk type's name as its string representation.
        """
        return str(self.name)
