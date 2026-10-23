from __future__ import annotations

"""Model for medication indication."""

from typing import TYPE_CHECKING, ClassVar, TypedDict, Any

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Disease,
        DiseaseClassificationChoice,
        Event,
        InformationSource,
        MedicationSchedule,
    )


class MedicationIndicationLinks(TypedDict):
    medication_schedules: list["MedicationSchedule"]
    diseases: list["Disease"]
    events: list["Event"]
    disease_classification_choices: list["DiseaseClassificationChoice"]


class MedicationIndicationManager(models.Manager["MedicationIndication"]):
    """Manager for the medication indication model."""

    def get_by_natural_key(self, name: str) -> "MedicationIndication":
        """Retrieve a medication indication by its natural key."""
        return self.get(name=name)


class MedicationIndication(models.Model):
    """Model representing a medication indication."""

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    indication_type: models.ForeignKey[Any] = models.ForeignKey(
        "MedicationIndicationType",
        on_delete=models.CASCADE,
        related_name="medication_indications",
    )
    medication_schedules: models.ManyToManyField[
        "MedicationSchedule",
        "MedicationSchedule",
    ] = models.ManyToManyField(
        "MedicationSchedule",
    )
    diseases: models.ManyToManyField["Disease", "Disease"] = models.ManyToManyField(
        "Disease"
    )
    events: models.ManyToManyField["Event", "Event"] = models.ManyToManyField("Event")
    disease_classification_choices: models.ManyToManyField[
        "DiseaseClassificationChoice",
        "DiseaseClassificationChoice",
    ] = models.ManyToManyField("DiseaseClassificationChoice")
    sources: models.ManyToManyField[
        "InformationSource",
        "InformationSource",
    ] = models.ManyToManyField("InformationSource")

    if TYPE_CHECKING:
        from endoreg_db.models import MedicationIndicationType

        @property
        def medication_indication_type(self) -> "MedicationIndicationType": ...

    def get_indication_links(self) -> MedicationIndicationLinks:
        """Return a dictionary of all linked objects for this medication indication."""
        links: MedicationIndicationLinks = {
            "medication_schedules": list(self.medication_schedules.all()),
            "diseases": list(self.diseases.all()),
            "events": list(self.events.all()),
            "disease_classification_choices": list(
                self.disease_classification_choices.all()
            ),
        }
        return links

    objects: ClassVar[MedicationIndicationManager] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        MedicationIndicationManager()
    )

    def natural_key(self) -> tuple[str]:
        """Return the natural key for the medication indication."""
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
