from typing import TYPE_CHECKING

from django.db import models


class RiskManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a risk instance using its natural key.

        Args:
            name: The unique name identifying the risk instance.

        Returns:
            The risk instance with the matching name.
        """
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
        from endoreg_db.models import RiskType

        risk_types: RiskType

    def natural_key(self):
        """
        Return a tuple containing the natural key of the risk instance.

        The tuple consists of the unique 'name' attribute, which enables natural key lookups
        and serialization within Django.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the risk.

        Returns:
            str: The risk's name.
        """
        return str(self.name)
