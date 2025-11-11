import numbers
from typing import TYPE_CHECKING, Optional

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.utils.links.requirement_link import RequirementLinks  # Added import

    from ...administration.person.patient import Patient
    from ...other.unit import Unit
    from ..laboratory import LabValue
    from .patient_lab_sample import PatientLabSample


class PatientLabValue(models.Model):
    """
    A class representing a patient lab value.

    Attributes:
        patient (Patient): The patient.
        lab_value (LabValue): The lab value.
        value (float): The value of the lab value.
        date (datetime): The date of the lab value.
    """

    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, related_name="lab_values", blank=True, null=True)
    lab_value = models.ForeignKey("LabValue", on_delete=models.CASCADE)
    value = models.FloatField(blank=True, null=True)
    value_str = models.CharField(max_length=255, blank=True, null=True)
    sample = models.ForeignKey("PatientLabSample", on_delete=models.CASCADE, blank=True, null=True, related_name="values")
    datetime = models.DateTimeField(  # if not set, use now
        auto_now_add=True
    )
    normal_range = models.JSONField(default=dict)
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE, blank=True, null=True)

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
        patient: models.ForeignKey["Patient|None"]
        lab_value: models.ForeignKey["LabValue"]
        unit: models.ForeignKey["Unit|None"]
        sample: models.ForeignKey["PatientLabSample|None"]
        normal_range: models.JSONField[dict]

    @classmethod
    def create_lab_value_by_sample(
        cls, sample: "PatientLabSample", lab_value_name: str, value: Optional[float] = None, value_str: Optional[str] = None, unit: Optional["Unit"] = None
    ):
        from ..laboratory import LabValue

        patient = sample.patient
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

    def __str__(self):
        formatted_datetime = self.datetime.strftime("%Y-%m-%d %H:%M")
        # normal_range = self.get_normal_range()
        norm_range_string = f"[{self.normal_range.get('min', '')} - {self.normal_range.get('max', '')}]"
        _str = f"{self.lab_value} - {self.value} {self.unit} - {norm_range_string} ({formatted_datetime})"
        return _str

    def get_normal_range(self):
        lab_value = self.lab_value_safe
        patient = self.patient_safe

        age = patient.age_safe
        gender = patient.gender

        normal_range_dict = lab_value.get_normal_range(age, gender)
        return normal_range_dict

    def set_min_norm_value(self, value, save=True):
        self.normal_range["min"] = value
        if save:
            self.save()

    def set_max_norm_value(self, value, save=True):
        self.normal_range["max"] = value
        if save:
            self.save()

    def set_norm_values_from_default(self):
        normal_range_dict = self.get_normal_range()
        self.set_min_norm_value(normal_range_dict["min"], save=False)
        self.set_max_norm_value(normal_range_dict["max"], save=False)
        self.save()

    def set_unit_from_default(self):
        _default_unit = self.lab_value_safe.default_unit
        self.unit = _default_unit
        self.save()

    def get_value(self):
        if self.value:
            return self.value
        else:
            return self.value_str

    def get_value_field_name(self):
        if self.value:
            return "value"
        else:
            return "value_str"

    # customize save method so that if a numeric value exists, we round it to the precision of the lab value
    def save(self, *args, **kwargs):
        if self.value is not None:
            # only attempt rounding for real numeric types (ints/floats/compatible)

            precision = getattr(self.lab_value_safe, "numeric_precision", None)
            if isinstance(self.value, numbers.Real) and precision is not None:
                # ensure a plain float is passed to built-in round to satisfy type checkers
                self.value = round(float(self.value), int(precision))
        super().save(*args, **kwargs)

    def set_value_by_distribution(self, distribution=None, save=True):
        import warnings

        from ...other.distribution import (
            DateValueDistribution,
            MultipleCategoricalValueDistribution,
            NumericValueDistribution,
            SingleCategoricalValueDistribution,
        )

        patient = self.patient_safe
        lab_value = self.lab_value_safe

        self.unit = self.lab_value_safe.default_unit

        if not distribution:
            distribution = lab_value.get_default_default_distribution()

            if not distribution:
                warnings.warn(f"No distribution set for lab value {lab_value}, assuming uniform numeric distribution based on normal values")

                if not self.normal_range.get("min", None) or not self.normal_range.get("max", None):
                    self.set_norm_values_from_default()
                _min = self.normal_range.get("min", 0.0001)
                _max = self.normal_range.get("max", 100)
                _name = "auto-" + self.lab_value_safe.name + "-distribution-default-uniform"
                distribution = NumericValueDistribution(name=_name, min_descriptor=_min, max_max_desciptor=_max, distribution_type="uniform")

                value = distribution.generate_value(lab_value=lab_value, patient=patient)
                self.value = value
                if save:
                    self.save()

                return value

        if isinstance(distribution, SingleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = value
            if save:
                self.save()
            return value

        elif isinstance(distribution, NumericValueDistribution):
            value = distribution.generate_value(lab_value=lab_value, patient=patient)
            self.value = value
            if save:
                self.save()
            return value

        elif isinstance(distribution, MultipleCategoricalValueDistribution):
            value = distribution.generate_value()
            self.value_str = value
            if save:
                self.save()
            return value

        elif isinstance(distribution, DateValueDistribution):
            # raise not implemented error
            value = distribution.generate_value()
            self.value = value
            if save:
                self.save()
            return value

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates and returns all related model instances relevant for requirement evaluation
        as a RequirementLinks object.
        """
        from endoreg_db.utils.links.requirement_link import RequirementLinks

        return RequirementLinks(
            patient_lab_values=[self]  # Include the lab value itself
        )
