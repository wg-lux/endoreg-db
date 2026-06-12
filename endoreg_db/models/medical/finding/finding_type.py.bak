from typing import TYPE_CHECKING

from django.db import models


class FindingTypeManager(models.Manager["FindingType"]):
    def get_by_natural_key(self, name: str) -> "FindingType":
        """
        Retrieve a FindingType instance by its unique name for natural key deserialization.

        Parameters:
            name (str): The unique name of the FindingType to retrieve.

        Returns:
            FindingType: The instance matching the given name.
        """
        return self.get(name=name)


class FindingType(models.Model):
    name: models.CharField[str, str] = models.CharField(max_length=100, unique=True)
    description: models.TextField[str | None, str | None] = models.TextField(
        blank=True, null=True
    )

    objects: models.Manager["FindingType"] = FindingTypeManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        from endoreg_db.models import FindingClassification

        @property
        def finding_classifications(
            self,
        ) -> "models.Manager[FindingClassification]": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
