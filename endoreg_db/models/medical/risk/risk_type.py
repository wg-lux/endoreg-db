from typing import TYPE_CHECKING

from django.db import models

# Deorecate ?


class RiskTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a RiskType instance using its natural key.

        Args:
            name (str): The unique name identifying the RiskType instance.

        Returns:
            RiskType: The matching instance with the provided name.
        """
        return self.get(name=name)


class RiskType(models.Model):
    """
    A class representing a risk type.

    Attributes:
        name (str): The name of the risk type.
        description (str): A description of the risk type.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = RiskTypeManager()

    if TYPE_CHECKING:
        from endoreg_db.models import Risk

        risks: models.QuerySet[Risk]

    def natural_key(self):
        """
        Return the natural key for this risk type.

        This method returns a tuple containing only the risk type's unique name, which is used
        to identify the instance naturally.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the risk type's name as its string representation.
        """
        return str(self.name)
