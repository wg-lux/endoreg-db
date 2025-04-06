from django.db import models
from typing import List, TYPE_CHECKING


class RiskManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Risk(models.Model):
    """
    A class representing a risk.

    Attributes:
        name (str): The name of the risk.
        name_de (str): The German name of the risk.
        name_en (str): The English name of the risk.
        description (str): A description of the risk.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    risk_value = models.FloatField(
        blank=True,
        null=True,
        help_text="Risk value for the risk. If not set, the risk is not used in calculations.",
    )

    risk_type = models.ForeignKey(
        "RiskType",
        on_delete=models.CASCADE,
        related_name="risks",
        blank=True,
        null=True,
    )

    objects = RiskManager()

    if TYPE_CHECKING:
        from endoreg_db.models.risk.risk_type import RiskType

        risk_types: RiskType

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)
