from __future__ import annotations
import numbers
from datetime import datetime as dt_datetime
from typing import TYPE_CHECKING, Protocol, cast, Any

from django.db import models
from lx_dtypes.models.contracts.lab_value import LabValueNormalRangePayload

if TYPE_CHECKING:
    from endoreg_db.utils.links import ModelLinks  # Added import

    from ...administration.person.patient.patient import Patient
    from endoreg_db.models.other.distribution.base_value_distribution import (
        BaseValueDistribution,
    )
    from endoreg_db.models.other.distribution.date_value_distribution import (
        DateValueDistribution,
    )
    from endoreg_db.models.other.distribution.multiple_categorical_value_distribution import (
        MultipleCategoricalValueDistribution,
    )
    from endoreg_db.models.other.distribution.numeric_value_distribution import (
        NumericValueDistribution,
    )
    from endoreg_db.models.other.distribution.single_categorical_value_distribution import (
        SingleCategoricalValueDistribution,
    )
    from endoreg_db.models.other.unit import Unit
    from ..laboratory.lab_value import LabValue
    from .patient_lab_sample import PatientLabSample


class _PatientLabSamplePatientLike(Protocol):
    patient: "Patient"


class _PatientLabValueGenderLike(Protocol):
    name: str


class PatientLabValue(models.Model):
    """
    A class representing a patient lab value.

    Attributes:
        patient (Patient): The patient.
        lab_value (LabValue): The lab value.
        value (float): The value of the lab value.
        date (datetime): The date of the lab value.
    """

    patient: models.ForeignKey["Patient | None"] = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="lab_values",
        blank=True,
        null=True,
    )
    lab_value: models.ForeignKey["LabValue"] = models.ForeignKey(
        "LabValue", on_delete=models.CASCADE
    )
    value: models.FloatField[Any, Any] = models.FloatField(
        blank=True,
        null=True,
    )
    value_str: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    sample: models.ForeignKey["PatientLabSample | None"] = models.ForeignKey(
        "PatientLabSample",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="values",
    )
    timestamp: models.DateTimeField[Any, Any] = (
        models.DateTimeField(  # if not set, use now
            auto_now_add=True
        )
    )
    normal_range: models.JSONField[Any, Any] = models.JSONField(default=dict)
    unit: models.ForeignKey["Unit | None"] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE, blank=True, null=True
    )

    @property
    def lab_value_safe(self) -> "LabValue":
        """Returns the lab value, raises error if not set."""
        if not self.lab_value:
            raise ValueError("Lab value is not set.")
        return self.lab_value

    @property
    def patient_safe(self) -> "Patient":
        """Returns the patient, raises error if not set."""
        if not self.patient:
            raise ValueError("Patient is not set.")
        return self.patient

    if TYPE_CHECKING:
        pass

    @classmethod
    def create_lab_value_by_sample(
        cls,
        sample: "PatientLabSample",
        lab_value_name: str,
        value: float | None = None,
        value_str: str | None = None,
        unit: "Unit | None" = None,
    ) -> "PatientLabValue":
        from ..laboratory import LabValue

        patient = cast(_PatientLabSamplePatientLike, sample).patient
        lab_value = LabValue.objects.get(name=lab_value_name)

        pat_lab_val = cls.objects.create(
            patient=patient,
            lab_value=lab_value,
            value=value,
            value_str=value_str,
            sample=sample,
            unit=unit,
        )

        pat_lab_val.save()

        return pat_lab_val

    def __str__(self) -> str:
        formatted_datetime = self.timestamp.strftime("%Y-%m-%d %H:%M")
        norm_range_string = (
            f"[{self.normal_range.min if self.normal_range.min is not None else ''}"
            f" - {self.normal_range.max if self.normal_range.max is not None else ''}]"
        )
        return (
            f"{self.lab_value} - {self.value} {self.unit} - "
            f"{norm_range_string} ({formatted_datetime})"
        )

    def get_normal_range(self) -> LabValueNormalRangePayload:
        lab_value = self.lab_value_safe
        patient = self.patient_safe

        age = patient.age_safe
        gender = cast(_PatientLabValueGenderLike | None, patient.gender)

        return lab_value.get_normal_range(age, gender)

    def set_min_norm_value(self, value: float | None, save: bool = True) -> None:
        self.normal_range = self.normal_range.model_copy(update={"min": value})
        if save:
            self.save()

    def set_max_norm_value(self, value: float | None, save: bool = True) -> None:
        self.normal_range = self.normal_range.model_copy(update={"max": value})
        if save:
            self.save()

    def set_norm_values_from_default(self) -> None:
        normal_range_dict = self.get_normal_range()
        self.set_min_norm_value(normal_range_dict.min, save=False)
        self.set_max_norm_value(normal_range_dict.max, save=False)
        self.save()

    def set_unit_from_default(self) -> None:
        self.unit = self.lab_value_safe.default_unit
        self.save()

    def get_value(self) -> float | str | dt_datetime | None:
        if self.value is not None:
            return self.value
        else:
            return self.value_str

    def get_value_field_name(self) -> str:
        if self.value is not None:
            return "value"
        else:
            return "value_str"

    # customize save method so that if a numeric value exists, we round it to the precision of the lab value
    def save(self, *args: object, **kwargs: object) -> None:
        if self.value is not None:
            # only attempt rounding for real numeric types (ints/floats/compatible)

            precision = getattr(self.lab_value_safe, "numeric_precision", None)
            if isinstance(self.value, numbers.Real) and precision is not None:
                # ensure a plain float is passed to built-in round to satisfy type checkers
                self.value = round(float(self.value), int(precision))
        super().save(*args, **kwargs)

    def set_value_by_distribution(
        self,
        distribution: "BaseValueDistribution | None" = None,
        save: bool = True,
    ) -> float | str | None:
        import warnings

        patient = self.patient_safe
        lab_value = self.lab_value_safe

        self.unit = self.lab_value_safe.default_unit

        if distribution is None:
            distribution = lab_value.get_default_default_distribution()

            if not distribution:
                warnings.warn(
                    f"No distribution set for lab value {lab_value}, assuming uniform numeric distribution based on normal values"
                )

                if self.normal_range.min is None or self.normal_range.max is None:
                    self.set_norm_values_from_default()
                _min = (
                    self.normal_range.min
                    if self.normal_range.min is not None
                    else 0.0001
                )
                _max = (
                    self.normal_range.max if self.normal_range.max is not None else 100
                )
                _name = (
                    "auto-" + self.lab_value_safe.name + "-distribution-default-uniform"
                )
                distribution = NumericValueDistribution(
                    name=_name,
                    min_descriptor=_min,
                    max_max_desciptor=_max,
                    distribution_type="uniform",
                )

                value = distribution.generate_value(
                    lab_value=lab_value, patient=patient
                )
                self.value = float(value)
                if save:
                    self.save()

                return float(value)

        if isinstance(distribution, SingleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = str(value)
            if save:
                self.save()
            return str(value)

        elif isinstance(distribution, NumericValueDistribution):
            value = distribution.generate_value(lab_value=lab_value, patient=patient)
            self.value = value
            if save:
                self.save()
            return float(value)

        elif isinstance(distribution, MultipleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = str(value)
            if save:
                self.save()
            return str(value)

        elif isinstance(distribution, DateValueDistribution):
            # raise not implemented error
            date_value = distribution.generate_value()
            self.value_str = date_value.isoformat()
            if save:
                self.save()
            return date_value.isoformat()

    @property
    def links(self) -> "ModelLinks":
        """
        Aggregates and returns all related model instances for linked-model traversal
        as a ModelLinks object.
        """
        from endoreg_db.utils.links import ModelLinks

        return ModelLinks(
            patient_lab_values=[self]  # Include the lab value itself
        )
