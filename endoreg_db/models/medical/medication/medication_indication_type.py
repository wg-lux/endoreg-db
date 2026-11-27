from __future__ import annotations

"""Model for medication indication type."""

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from endoreg_db.models import MedicationIndication


class MedicationIndicationType(models.Model):
    """Model representing a medication indication type."""

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    objects = NameNaturalKeyManager["MedicationIndicationType"]()

    if TYPE_CHECKING:

        @property
        def medication_indications(
            self,
        ) -> "models.Manager[MedicationIndication]": ...

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the medication indication type."""
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    @classmethod
    def get_random_indication_by_type(cls, name: str) -> "MedicationIndication":
        """Return a random medication indication of the given type."""
        med_indication = (
            cls.objects.get(name=name).medication_indications.order_by("?").first()
        )
        if not med_indication:
            raise cls.DoesNotExist(f"No medication indication found for type: {name}")
        return med_indication

    def get_random_medication_indication(self) -> "MedicationIndication":
        """Return a random medication indication of this type."""
        from endoreg_db.models import MedicationIndication

        med_indication = (
            MedicationIndication.objects.filter(indication_type=self)
            .order_by("?")
            .first()
        )
        if med_indication is None:
            raise MedicationIndication.DoesNotExist(
                f"No medication indication found for type: {self.name}"
            )
        return med_indication
