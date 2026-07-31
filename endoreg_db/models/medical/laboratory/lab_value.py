from __future__ import annotations

from dataclasses import dataclass
import warnings
from numbers import Real
from typing import TYPE_CHECKING, cast, Any

from django.db import models
from django.core.exceptions import ValidationError
from lx_dtypes.models.contracts.lab_value import (
    LabValueNormalRangeBandPayload,
    LabValueNormalRangePayload,
)
from endoreg_db.schemas import validate_lab_value_normal_range

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


def _resolve_gender_name(gender: object | None, *, lab_value_name: str) -> str:
    gender_name = cast(str | None, getattr(gender, "name", None))
    if not gender_name:
        warnings.warn(
            f"Gender not provided for gender-dependent LabValue '{lab_value_name}'. Defaulting to 'male' range.",
            UserWarning,
        )
        return "male"
    if gender_name in {"male", "female", "other"}:
        return gender_name
    warnings.warn(
        f"Normal range for gender '{gender_name}' not found for LabValue '{lab_value_name}'. Defaulting to 'male' range.",
        UserWarning,
    )
    return "male"


def _gender_range(
    source: LabValueNormalRangePayload,
    *,
    gender_name: str,
) -> LabValueNormalRangeBandPayload | None:
    return {
        "male": source.male,
        "female": source.female,
        "other": source.other,
    }[gender_name]


def _resolve_gender_bounds(
    source: LabValueNormalRangePayload,
    *,
    gender: object | None,
    gender_dependent: bool,
    lab_value_name: str,
) -> tuple[float | None, float | None, str | None]:
    if not gender_dependent:
        return None, None, None
    gender_name = _resolve_gender_name(gender, lab_value_name=lab_value_name)
    gender_specific_range = _gender_range(source, gender_name=gender_name)
    if gender_specific_range is None:
        warnings.warn(
            f"No gender-specific data found for '{gender_name}' in LabValue '{lab_value_name}'. Falling back to general range if available.",
            UserWarning,
        )
        return None, None, gender_name
    return gender_specific_range.min, gender_specific_range.max, gender_name


def _fill_general_bounds(
    source: LabValueNormalRangePayload,
    *,
    minimum: float | None,
    maximum: float | None,
) -> tuple[float | None, float | None]:
    resolved_minimum = source.min if minimum is None else minimum
    resolved_maximum = source.max if maximum is None else maximum
    return resolved_minimum, resolved_maximum


def _warn_unimplemented_range_variants(
    *,
    lab_value_name: str,
    age: int | None,
    age_dependent: bool,
    special_case: bool,
) -> None:
    if age_dependent:
        warnings.warn(
            f"Age dependent normal range not implemented yet for LabValue '{lab_value_name}'. Age: {age}."
        )
    if special_case:
        warnings.warn(
            f"Special case normal range not implemented yet for LabValue '{lab_value_name}'."
        )


def _normal_range_gender_context(
    gender: object | None,
    *,
    gender_name_used: str | None,
) -> str:
    gender_repr = cast(str | None, getattr(gender, "name", None)) or "None"
    if gender_name_used and gender_name_used != gender_repr:
        return f"{gender_repr} (lookup attempted for: {gender_name_used})"
    return gender_repr


def _normal_range_context(
    *,
    gender: object | None,
    gender_name_used: str | None,
    gender_dependent: bool,
    age: int | None,
    age_dependent: bool,
) -> list[str]:
    context_parts: list[str] = []
    if gender_dependent:
        gender_repr = _normal_range_gender_context(
            gender,
            gender_name_used=gender_name_used,
        )
        context_parts.append(f"gender: {gender_repr}")
    if age_dependent:
        context_parts.append(f"age: {age}")
    return context_parts


def _warn_missing_minimum(
    *,
    lab_value_name: str,
    gender: object | None,
    gender_name_used: str | None,
    gender_dependent: bool,
    age: int | None,
    age_dependent: bool,
) -> None:
    context_parts = _normal_range_context(
        gender=gender,
        gender_name_used=gender_name_used,
        gender_dependent=gender_dependent,
        age=age,
        age_dependent=age_dependent,
    )
    warning_message = (
        f"Could not determine a 'min' normal range for LabValue '{lab_value_name}'"
    )
    if context_parts:
        warning_message += f" with context ({', '.join(context_parts)})."
    else:
        warning_message += " (general context)."
    warning_message += " Check LabValue's default_normal_range definition."
    warnings.warn(warning_message, UserWarning)


def _value_is_within_bounds(
    value: float,
    *,
    lower_bound: float | None,
    upper_bound: float | None,
) -> bool:
    if lower_bound is not None and upper_bound is not None:
        return lower_bound <= value <= upper_bound
    if lower_bound is not None:
        return value >= lower_bound
    if upper_bound is not None:
        return value <= upper_bound
    return True


def _normal_range_fallback(
    *,
    lower_bound: float | None,
    upper_bound: float | None,
) -> float | None:
    if lower_bound is not None and upper_bound is not None:
        return (lower_bound + upper_bound) / 2.0
    if lower_bound is not None:
        return lower_bound
    return upper_bound


def _generate_normal_value(
    distribution: "NumericValueDistribution",
    *,
    lab_value: "LabValue",
    patient: "Patient",
    lower_bound: float | None,
    upper_bound: float | None,
) -> float:
    for _ in range(10):
        generated_value = _generate_numeric_lab_value(
            distribution,
            lab_value=lab_value,
            patient=patient,
        )
        if _value_is_within_bounds(
            generated_value,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        ):
            return generated_value
    if lower_bound is not None and upper_bound is not None:
        return (lower_bound + upper_bound) / 2.0
    return _generate_numeric_lab_value(
        distribution,
        lab_value=lab_value,
        patient=patient,
    )


def _normal_value_without_patient(
    *,
    lab_value_name: str,
    lower_bound: float | None,
    upper_bound: float | None,
) -> float | None:
    warnings.warn(
        f"Cannot use numerical distribution for {lab_value_name} without patient context. Falling back to normal range logic for normal value."
    )
    fallback = _normal_range_fallback(
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    if fallback is not None:
        return fallback
    warnings.warn(
        f"Cannot determine a normal value for {lab_value_name} without a normal range or patient context for distribution.",
        UserWarning,
    )
    return None


def _normal_value_without_distribution(
    *,
    lab_value_name: str,
    lower_bound: float | None,
    upper_bound: float | None,
) -> float | None:
    fallback = _normal_range_fallback(
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    if fallback is not None:
        return fallback
    warnings.warn(
        f"Cannot determine a normal value for {lab_value_name} without a numerical distribution or a normal range."
    )
    return None


@dataclass(frozen=True, slots=True)
class CommonLabValues:
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


class LabValueManager(models.Manager["LabValue"]):
    def get_by_natural_key(self, name: str) -> "LabValue":
        return self.get(name=name)


class LabValue(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    abbreviation: models.CharField[Any, Any] = models.CharField(
        max_length=10, blank=True, null=True
    )
    default_unit: models.ForeignKey["Unit | None"] = models.ForeignKey(
        "Unit", on_delete=models.CASCADE, blank=True, null=True
    )
    numeric_precision: models.IntegerField[Any, Any] = models.IntegerField(default=3)
    default_single_categorical_value_distribution: models.ForeignKey[
        "SingleCategoricalValueDistribution | None"
    ] = models.ForeignKey(
        "SingleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_single_categorical_value_distribution",
    )
    default_numerical_value_distribution: models.ForeignKey[
        "NumericValueDistribution | None"
    ] = models.ForeignKey(
        "NumericValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_numerical_value_distribution",
    )
    default_multiple_categorical_value_distribution: models.ForeignKey[
        "MultipleCategoricalValueDistribution | None"
    ] = models.ForeignKey(
        "MultipleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_multiple_categorical_value_distribution",
    )
    default_date_value_distribution: models.ForeignKey[
        "DateValueDistribution | None"
    ] = models.ForeignKey(
        "DateValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_date_value_distribution",
    )
    default_normal_range = models.JSONField(blank=True, null=True)
    normal_range_age_dependent: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    normal_range_gender_dependent: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    normal_range_special_case: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    bound_adjustment_factor: models.FloatField[Any, Any] = models.FloatField(
        default=0.1,
        help_text="Factor for adjusting bounds when generating increased/decreased values, e.g., 0.1 for 10%.",
    )
    objects = LabValueManager()

    def clean(self) -> None:
        super().clean()
        try:
            self.default_normal_range = validate_lab_value_normal_range(
                self.default_normal_range, allow_none=True
            )
        except ValueError as exc:
            raise ValidationError({"default_normal_range": str(exc)}) from exc

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

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
        current_range_source = LabValueNormalRangePayload.model_validate(
            self.default_normal_range or {}
        )
        min_value, max_value, gender_name_used = _resolve_gender_bounds(
            current_range_source,
            gender=gender,
            gender_dependent=self.normal_range_gender_dependent,
            lab_value_name=self.name,
        )
        min_value, max_value = _fill_general_bounds(
            current_range_source,
            minimum=min_value,
            maximum=max_value,
        )
        _warn_unimplemented_range_variants(
            lab_value_name=self.name,
            age=age,
            age_dependent=self.normal_range_age_dependent,
            special_case=self.normal_range_special_case,
        )
        if min_value is None and max_value is None:
            return LabValueNormalRangePayload(min=None, max=None)
        if min_value is None:
            _warn_missing_minimum(
                lab_value_name=self.name,
                gender=gender,
                gender_name_used=gender_name_used,
                gender_dependent=self.normal_range_gender_dependent,
                age=age,
                age_dependent=self.normal_range_age_dependent,
            )
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
            if patient:
                return _generate_normal_value(
                    distribution,
                    lab_value=self,
                    patient=patient,
                    lower_bound=lower_bound,
                    upper_bound=upper_bound,
                )
            return _normal_value_without_patient(
                lab_value_name=self.name,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            )
        return _normal_value_without_distribution(
            lab_value_name=self.name,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

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
