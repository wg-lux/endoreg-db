from __future__ import annotations

import warnings
from numbers import Real
from typing import TYPE_CHECKING, cast

from django.db import models
from lx_dtypes.models.contracts.lab_value import (
    LabValueNormalRangeBandPayload,
    LabValueNormalRangePayload,
)
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient import Patient
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

LANG = "de"


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return float(value)


def _generate_numeric_lab_value(
    distribution: "NumericValueDistribution",
    *,
    lab_value: "LabValue",
    patient: "Patient",
) -> float:
    generated_value = distribution.generate_value(lab_value=lab_value, patient=patient)
    numeric_value = _as_float(generated_value)
    if numeric_value is None:
        raise TypeError(
            f"Numerical distribution for LabValue '{lab_value.name}' returned a non-numeric value."
        )
    return numeric_value


class CommonLabValues(BaseModel):
    """Structured lookup for common laboratory values."""

    hb: "LabValue"
    wbc: "LabValue"
    plt: "LabValue"
    cr: "LabValue"
    na: "LabValue"
    k: "LabValue"
    glc: "LabValue"
    inr: "LabValue"
    crp: "LabValue"

    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)


class LabValueManager(models.Manager["LabValue"]):
    def get_by_natural_key(self, name: str) -> "LabValue":
        return self.get(name=name)


class LabValue(models.Model):
    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    abbreviation: models.CharField[str | None, str | None] = models.CharField(
        max_length=10, blank=True, null=True
    )
    default_unit: models.ForeignKey["Unit | None", "Unit | None"] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE, blank=True, null=True
    )
    numeric_precision: models.IntegerField[int, int] = models.IntegerField(default=3)
    default_single_categorical_value_distribution: models.ForeignKey[
        "SingleCategoricalValueDistribution | None",
        "SingleCategoricalValueDistribution | None",
    ] = models.ForeignKey(
        "SingleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_single_categorical_value_distribution",
    )
    default_numerical_value_distribution: models.ForeignKey[
        "NumericValueDistribution | None",
        "NumericValueDistribution | None",
    ] = models.ForeignKey(
        "NumericValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_numerical_value_distribution",
    )
    default_multiple_categorical_value_distribution: models.ForeignKey[
        "MultipleCategoricalValueDistribution | None",
        "MultipleCategoricalValueDistribution | None",
    ] = models.ForeignKey(
        "MultipleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_multiple_categorical_value_distribution",
    )
    default_date_value_distribution: models.ForeignKey[
        "DateValueDistribution | None", "DateValueDistribution | None"
    ] = models.ForeignKey(
        "DateValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_date_value_distribution",
    )
    default_normal_range: models.JSONField[
        LabValueNormalRangePayload | None, LabValueNormalRangePayload | None
    ] = models.JSONField(blank=True, null=True)
    normal_range_age_dependent: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    normal_range_gender_dependent: models.BooleanField[bool, bool] = (
        models.BooleanField(default=False)
    )
    normal_range_special_case: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )
    bound_adjustment_factor: models.FloatField[float, float] = models.FloatField(
        default=0.1,
        help_text="Factor for adjusting bounds when generating increased/decreased values, e.g., 0.1 for 10%.",
    )
    objects = LabValueManager()

    @classmethod
    def get_common_lab_values(cls) -> CommonLabValues:
        return CommonLabValues(
            hb=cls.objects.get(name="hemoglobin"),
            wbc=cls.objects.get(name="white_blood_cells"),
            plt=cls.objects.get(name="platelets"),
            cr=cls.objects.get(name="creatinine"),
            na=cls.objects.get(name="sodium"),
            k=cls.objects.get(name="potassium"),
            glc=cls.objects.get(name="glucose"),
            inr=cls.objects.get(name="international_normalized_ratio"),
            crp=cls.objects.get(name="c_reactive_protein"),
        )

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    def get_default_default_distribution(self) -> BaseValueDistribution | None:
        if self.default_single_categorical_value_distribution:
            return self.default_single_categorical_value_distribution
        if self.default_numerical_value_distribution:
            return self.default_numerical_value_distribution
        if self.default_multiple_categorical_value_distribution:
            return self.default_multiple_categorical_value_distribution
        if self.default_date_value_distribution:
            return self.default_date_value_distribution
        warnings.warn("No default distribution set for lab value")
        return None

    def get_normal_range(
        self, age: int | None = None, gender: object | None = None
    ) -> LabValueNormalRangePayload:
        age_dependent = self.normal_range_age_dependent
        gender_dependent = self.normal_range_gender_dependent
        special_case = self.normal_range_special_case

        min_value: float | None = None
        max_value: float | None = None
        current_range_source = LabValueNormalRangePayload.model_validate(
            self.default_normal_range or {}
        )

        gender_name_to_use: str | None = None
        if gender_dependent:
            gender_name = cast(str | None, getattr(gender, "name", None))
            if gender_name:
                gender_name_to_use = gender_name
                if gender_name_to_use not in {"male", "female", "other"}:
                    warnings.warn(
                        f"Normal range for gender '{gender_name_to_use}' not found for LabValue '{self.name}'. Defaulting to 'male' range.",
                        UserWarning,
                    )
                    gender_name_to_use = "male"
            else:
                warnings.warn(
                    f"Gender not provided for gender-dependent LabValue '{self.name}'. Defaulting to 'male' range.",
                    UserWarning,
                )
                gender_name_to_use = "male"

            gender_specific_range: LabValueNormalRangeBandPayload | None
            if gender_name_to_use == "male":
                gender_specific_range = current_range_source.male
            elif gender_name_to_use == "female":
                gender_specific_range = current_range_source.female
            else:
                gender_specific_range = current_range_source.other
            if gender_specific_range is not None:
                min_value = gender_specific_range.min
                max_value = gender_specific_range.max
            else:
                warnings.warn(
                    f"No gender-specific data found for '{gender_name_to_use}' in LabValue '{self.name}'. Falling back to general range if available.",
                    UserWarning,
                )

        if min_value is None:
            min_value = current_range_source.min
        if max_value is None:
            max_value = current_range_source.max

        if age_dependent:
            warnings.warn(
                f"Age dependent normal range not implemented yet for LabValue '{self.name}'. Age: {age}."
            )

        if special_case:
            warnings.warn(
                f"Special case normal range not implemented yet for LabValue '{self.name}'."
            )

        if min_value is None and max_value is None:
            return LabValueNormalRangePayload(min=None, max=None)
        if min_value is None:
            context_parts: list[str] = []
            if gender_dependent:
                gender_repr = cast(str | None, getattr(gender, "name", None))
                if gender_repr is None:
                    gender_repr = "None"
                if gender_name_to_use and gender_name_to_use != gender_repr:
                    gender_repr = (
                        f"{gender_repr} (lookup attempted for: {gender_name_to_use})"
                    )
                context_parts.append(f"gender: {gender_repr}")
            if age_dependent:
                context_parts.append(f"age: {age}")

            warning_message = (
                f"Could not determine a 'min' normal range for LabValue '{self.name}'"
            )
            if context_parts:
                warning_message += f" with context ({', '.join(context_parts)})."
            else:
                warning_message += " (general context)."
            warning_message += " Check LabValue's default_normal_range definition."
            warnings.warn(warning_message, UserWarning)

        return LabValueNormalRangePayload(min=min_value, max=max_value)

    def get_increased_value(self, patient: Patient | None = None) -> float | None:
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        upper_bound = normal_range.max

        distribution = self.default_numerical_value_distribution
        if distribution is not None:
            numeric_distribution = distribution
            if patient:
                generated_value: float | None = None
                for _ in range(10):
                    generated_value = _generate_numeric_lab_value(
                        numeric_distribution,
                        lab_value=self,
                        patient=patient,
                    )
                    if upper_bound is not None:
                        if generated_value > upper_bound:
                            return generated_value
                    else:
                        mean = _as_float(getattr(numeric_distribution, "mean", None))
                        std_dev = _as_float(
                            getattr(numeric_distribution, "std_dev", None)
                        )
                        if std_dev is None:
                            std_dev = _as_float(
                                getattr(numeric_distribution, "stddev", None)
                            )
                        if (
                            mean is not None
                            and std_dev is not None
                            and generated_value > (mean + std_dev)
                        ):
                            return generated_value
                if upper_bound is not None:
                    return upper_bound + (
                        abs(upper_bound * self.bound_adjustment_factor)
                        if upper_bound != 0
                        else 1
                    )
                return _generate_numeric_lab_value(
                    distribution,
                    lab_value=self,
                    patient=patient,
                )
            warnings.warn(
                f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for increased value."
            )
            if upper_bound is not None:
                return upper_bound + (
                    abs(upper_bound * self.bound_adjustment_factor)
                    if upper_bound != 0
                    else 1
                )
            warnings.warn(
                f"Cannot determine an increased value for {self.name} without an upper normal range or patient context for distribution."
            )
            return None

        if upper_bound is not None:
            return upper_bound + (
                abs(upper_bound * self.bound_adjustment_factor)
                if upper_bound != 0
                else 1
            )
        warnings.warn(
            f"Cannot determine an increased value for {self.name} without a numerical distribution or an upper normal range."
        )
        return None

    def get_normal_value(self, patient: Patient | None = None) -> float | None:
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        lower_bound = normal_range.min
        upper_bound = normal_range.max

        distribution = self.default_numerical_value_distribution
        if distribution is not None:
            numeric_distribution = distribution
            if patient:
                for _ in range(10):
                    generated_value = _generate_numeric_lab_value(
                        numeric_distribution,
                        lab_value=self,
                        patient=patient,
                    )
                    if lower_bound is not None and upper_bound is not None:
                        if lower_bound <= generated_value <= upper_bound:
                            return generated_value
                    elif lower_bound is not None and generated_value >= lower_bound:
                        return generated_value
                    elif upper_bound is not None and generated_value <= upper_bound:
                        return generated_value
                    elif lower_bound is None and upper_bound is None:
                        return generated_value
                if lower_bound is not None and upper_bound is not None:
                    return (lower_bound + upper_bound) / 2.0
                return _generate_numeric_lab_value(
                    distribution,
                    lab_value=self,
                    patient=patient,
                )
            warnings.warn(
                f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for normal value."
            )
            if lower_bound is not None and upper_bound is not None:
                return (lower_bound + upper_bound) / 2.0
            if lower_bound is not None:
                return lower_bound
            if upper_bound is not None:
                return upper_bound
            warnings.warn(
                f"Cannot determine a normal value for {self.name} without a normal range or patient context for distribution.",
                UserWarning,
            )
            return None

        if lower_bound is not None and upper_bound is not None:
            return (lower_bound + upper_bound) / 2.0
        if lower_bound is not None:
            return lower_bound
        if upper_bound is not None:
            return upper_bound
        warnings.warn(
            f"Cannot determine a normal value for {self.name} without a numerical distribution or a normal range."
        )
        return None

    def get_decreased_value(self, patient: Patient | None = None) -> float | None:
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        lower_bound = normal_range.min

        distribution = self.default_numerical_value_distribution
        if distribution is not None:
            numeric_distribution = distribution
            mean = _as_float(getattr(numeric_distribution, "mean", None))
            std_dev = _as_float(getattr(numeric_distribution, "std_dev", None))
            if std_dev is None:
                std_dev = _as_float(getattr(numeric_distribution, "stddev", None))
            if patient:
                generated_value: float | None = None
                for _ in range(10):
                    generated_value = _generate_numeric_lab_value(
                        numeric_distribution,
                        lab_value=self,
                        patient=patient,
                    )
                    if lower_bound is not None:
                        if generated_value < lower_bound:
                            return generated_value
                    elif (
                        mean is not None
                        and std_dev is not None
                        and generated_value < (mean - std_dev)
                    ):
                        return generated_value
                if lower_bound is not None:
                    return lower_bound - (
                        abs(lower_bound * self.bound_adjustment_factor)
                        if lower_bound != 0
                        else 1
                    )
                return _generate_numeric_lab_value(
                    distribution,
                    lab_value=self,
                    patient=patient,
                )
            warnings.warn(
                f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for decreased value."
            )
            if lower_bound is not None:
                return lower_bound - (
                    abs(lower_bound * self.bound_adjustment_factor)
                    if lower_bound != 0
                    else 1
                )
            warnings.warn(
                f"Cannot determine a decreased value for {self.name} without a lower normal range or patient context for distribution."
            )
            return None

        if lower_bound is not None:
            return lower_bound - (
                abs(lower_bound * self.bound_adjustment_factor)
                if lower_bound != 0
                else 1
            )
        warnings.warn(
            f"Cannot determine a decreased value for {self.name} without a numerical distribution or a lower normal range."
        )
        return None
