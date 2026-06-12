from __future__ import annotations
from datetime import datetime as dt
from datetime import timezone
from typing import TYPE_CHECKING, ClassVar

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        LabValue,
        Patient,
        PatientLabSampleType,
        PatientLabValue,
    )
    from endoreg_db.utils.links import ModelLinks  # Added import

DEFAULT_PATIENT_LAB_SAMPLE_TYPE_NAME = "generic"


class PatientLabSampleTypeManager(models.Manager["PatientLabSampleType"]):
    """Manager for PatientLabSampleType with natural key support."""

    def get_by_natural_key(self, name: str) -> "PatientLabSampleType":
        """Retrieves a PatientLabSampleType instance by its natural key (name)."""
        return self.get(name=name)


class PatientLabSampleType(models.Model):
    """
    Represents the type of a patient lab sample (e.g., Blood, Urine).

    Attributes:
        name (str): The name of the patient lab sample type.
        description (str): A description of the patient lab sample type.
    """

    name: models.CharField[str, str] = models.CharField(max_length=255)
    description: models.TextField[str | None, str | None] = models.TextField(
        blank=True, null=True
    )

    objects: ClassVar[PatientLabSampleTypeManager] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        PatientLabSampleTypeManager()
    )

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the sample type."""
        return str(self.name)

    @classmethod
    def get_default_sample_type(cls) -> "PatientLabSampleType":
        """Gets or creates the default patient lab sample type ('default')."""
        return cls.objects.get_or_create(name="default")[0]


class PatientLabSample(models.Model):
    """
    Represents a specific lab sample taken from a patient at a certain date and time.

    Links to the patient, sample type, and associated lab values.

    Attributes:
        patient (Patient): The patient to which the lab sample belongs.
        sample_type (PatientLabSampleType): The type of the lab sample.
        date (datetime): The date of the lab sample.
        values (PatientLabValue; One2Many): The value of the lab sample.
    """

    patient: models.ForeignKey["Patient", "Patient"] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="lab_samples"
    )
    sample_type: models.ForeignKey[
        "PatientLabSampleType",
        "PatientLabSampleType",
    ] = models.ForeignKey("PatientLabSampleType", on_delete=models.CASCADE)
    date: models.DateTimeField[dt, dt] = models.DateTimeField()

    if TYPE_CHECKING:

        @property
        def values(self) -> models.Manager["PatientLabValue"]: ...

    def __str__(self) -> str:
        """Returns a string representation including patient, type, and date."""
        formatted_datetime = self.date.strftime("%Y-%m-%d %H:%M")
        return f"{self.patient} - {self.sample_type} - {formatted_datetime} ()"

    def get_values(self) -> models.QuerySet["PatientLabValue"]:
        """Returns all PatientLabValue instances associated with this sample."""
        return self.values.all()

    @property
    def links(self) -> "ModelLinks":
        """
        Aggregates and returns all related model instances for linked-model traversal
        as a ModelLinks object.
        """
        from endoreg_db.utils.links import ModelLinks
        # Assuming PatientLabValue is already imported or accessible
        # from .patient_lab_value import PatientLabValue # If direct import needed and not circular

        patient_lab_values = list(self.values.all())

        return ModelLinks.model_validate(
            {
                "patient_lab_values": patient_lab_values,
                "patient_lab_samples": [self],
            }
        )

    @classmethod
    def create_by_patient(
        cls,
        patient: "Patient | None" = None,
        sample_type: "PatientLabSampleType | str | None" = None,
        date: dt | None = None,
        save: bool = True,
    ) -> "PatientLabSample | None":
        """
        Creates a new patient lab sample for a given patient.

        Uses default type and current time if not provided.

        Args:
            patient (Patient): The patient to which the lab sample belongs.
            sample_type (PatientLabSampleType): The type of the lab sample.
            date (datetime): The date of the lab sample.
            save (bool): Whether to save the instance after creation.

        Returns:
            PatientLabSample: The new patient lab sample.
        """
        from warnings import warn

        if not patient:
            warn("No patient given. Cannot create patient lab sample.")
            return None
        if not sample_type:
            sample_type = PatientLabSampleType.get_default_sample_type()
        elif isinstance(sample_type, str):
            sample_type = PatientLabSampleType.objects.get(name=sample_type)
        if not date:
            date = dt.now(timezone.utc)

        patient_lab_sample = cls.objects.create(
            patient=patient, sample_type=sample_type, date=date
        )

        if save:
            patient_lab_sample.save()

        return patient_lab_sample

    def add_empty_value(self, lab_value: "LabValue") -> "PatientLabValue":
        """
        Adds an empty PatientLabValue for the given lab value to this sample.

        Args:
            lab_value (LabValue): The lab value to add.
        """
        from endoreg_db.models import PatientLabValue

        patient_lab_value = PatientLabValue.create_lab_value_by_sample(
            sample=self,
            lab_value_name=lab_value.name,
            value=None,  # Empty value
            value_str=None,  # Empty string
            unit=lab_value.default_unit,  # Use the unit from the lab value
        )
        return patient_lab_value
