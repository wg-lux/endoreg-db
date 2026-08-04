from django.db import models


class WasteManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Waste(models.Model):
    objects = WasteManager()

    name = models.CharField(max_length=255)
    # emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    def natural_key(self):
        """
        Return a tuple containing the unique natural key for this Waste instance.

        Returns:
            tuple: A single-element tuple with the waste's name, used for natural key serialization.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the name of the waste as its string representation.
        """
        return self.name
