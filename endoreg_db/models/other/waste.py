from typing import ClassVar

from django.db import models


class WasteManager(models.Manager["Waste"]):
    def get_by_natural_key(self, name: str) -> "Waste":
        return self.get(name=name)


class Waste(models.Model):
    objects: ClassVar[WasteManager] = WasteManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    name: models.CharField[str, str] = models.CharField(max_length=255)
    # emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple containing the unique natural key for this Waste instance.

        Returns:
            tuple: A single-element tuple with the waste's name, used for natural key serialization.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return the name of the waste as its string representation.
        """
        return str(self.name)
