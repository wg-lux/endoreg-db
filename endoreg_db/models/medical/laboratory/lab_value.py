import warnings
from typing import TYPE_CHECKING, Optional

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        DateValueDistribution,
        Gender,
        MultipleCategoricalValueDistribution,
        NumericValueDistribution,
        Patient,
        SingleCategoricalValueDistribution,
        Unit,
    )

LANG = "de"

from pydantic import BaseModel, ConfigDict


class CommonLabValues(BaseModel):
    """
    A Pydantic model representing a lookup for common lab values.
    It is used to provide a structured way to access common lab values like
    hemoglobin, creatinine, and others
    """

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


class LabValueManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a LabValue instance by its unique name.

        Args:
            name: The unique name of the LabValue.

        Returns:
            The LabValue instance with the specified name.
        """
        return self.get(name=name)


class LabValue(models.Model):
    name = models.CharField(max_length=255, unique=True)
    abbreviation = models.CharField(max_length=10, blank=True, null=True)
    default_unit = models.ForeignKey("Unit", on_delete=models.CASCADE, blank=True, null=True)
    numeric_precision = models.IntegerField(default=3)
    default_single_categorical_value_distribution = models.ForeignKey(
        "SingleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_single_categorical_value_distribution",
    )
    default_numerical_value_distribution = models.ForeignKey(
        "NumericValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_numerical_value_distribution",
    )
    default_multiple_categorical_value_distribution = models.ForeignKey(
        "MultipleCategoricalValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_multiple_categorical_value_distribution",
    )
    default_date_value_distribution = models.ForeignKey(
        "DateValueDistribution",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="default_date_value_distribution",
    )
    default_normal_range = models.JSONField(blank=True, null=True)
    normal_range_age_dependent = models.BooleanField(default=False)
    normal_range_gender_dependent = models.BooleanField(default=False)
    normal_range_special_case = models.BooleanField(default=False)
    bound_adjustment_factor = models.FloatField(
        default=0.1, help_text="Factor for adjusting bounds when generating increased/decreased values, e.g., 0.1 for 10%."
    )
    objects = LabValueManager()

    if TYPE_CHECKING:
        default_unit: models.ForeignKey["Unit|None"]
        default_single_categorical_value_distribution: models.ForeignKey["SingleCategoricalValueDistribution|None"]
        default_numerical_value_distribution: models.ForeignKey["NumericValueDistribution|None"]
        default_multiple_categorical_value_distribution: models.ForeignKey["MultipleCategoricalValueDistribution|None"]
        default_date_value_distribution: models.ForeignKey["DateValueDistribution|None"]

    @classmethod
    def get_common_lab_values(cls):
        """
        Retrieves a structured set of common laboratory values as a CommonLabValues instance.

        Returns:
            A CommonLabValues Pydantic model populated with LabValue objects for hemoglobin, white blood cells, platelets, creatinine, sodium, potassium, glucose, international normalized ratio, and C-reactive protein.
        """
        from endoreg_db.models.medical.laboratory.lab_value import CommonLabValues

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

    def natural_key(self):
        """Returns a tuple containing the unique name of this lab value instance."""
        return (self.name,)

    def __str__(self):
        """
        Return the lab value name as a string.

        Converts the lab value's `name` attribute into its string representation for
        display purposes.
        """
        return str(self.name)

    def get_default_default_distribution(self):
        """
        Returns the first available default distribution for the lab value.

        Checks the default distribution fields in the following order:
        default_single_categorical_value_distribution, default_numerical_value_distribution,
        default_multiple_categorical_value_distribution, and default_date_value_distribution.
        If none are set, a warning is issued and None is returned.
        """
        if self.default_single_categorical_value_distribution:
            return self.default_single_categorical_value_distribution
        elif self.default_numerical_value_distribution:
            return self.default_numerical_value_distribution
        elif self.default_multiple_categorical_value_distribution:
            return self.default_multiple_categorical_value_distribution
        elif self.default_date_value_distribution:
            return self.default_date_value_distribution
        else:
            warnings.warn("No default distribution set for lab value")
            return None

    def get_normal_range(self, age: Optional[int] = None, gender: Optional["Gender"] = None):
        """
        Returns the normal range for this lab value, considering age and gender dependencies.

        If the normal range is gender-dependent, attempts to use the provided gender; defaults to "male" if gender is missing or unknown. Falls back to general min/max values if gender-specific data is unavailable. Issues warnings for unimplemented age-dependent or special case ranges, and when min or max values cannot be determined. Returns a dictionary with keys "min" and "max", which may be None if the range is not defined.
        """
        from endoreg_db.models import Gender

        assert isinstance(age, int) or age is None
        assert isinstance(gender, Gender) or gender is None

        age_dependent = self.normal_range_age_dependent
        gender_dependent = self.normal_range_gender_dependent
        special_case = self.normal_range_special_case

        min_value = None
        max_value = None
        current_range_source = self.default_normal_range or {}

        gender_name_to_use = None
        if gender_dependent:
            if gender and hasattr(gender, "name") and gender.name:
                gender_name_to_use = gender.name
                if gender_name_to_use not in current_range_source:
                    warnings.warn(
                        f"Normal range for gender '{gender_name_to_use}' not found for LabValue '{self.name}'. Defaulting to 'male' range.", UserWarning
                    )
                    gender_name_to_use = "male"
            else:
                warnings.warn(f"Gender not provided for gender-dependent LabValue '{self.name}'. Defaulting to 'male' range.", UserWarning)
                gender_name_to_use = "male"

            # Attempt gender-specific lookup
            gender_specific_data = current_range_source.get(gender_name_to_use)
            if isinstance(gender_specific_data, dict):
                min_value = gender_specific_data.get("min")
                max_value = gender_specific_data.get("max")
            else:
                warnings.warn(
                    f"No gender-specific data found for '{gender_name_to_use}' in LabValue '{self.name}'. Falling back to general range if available.",
                    UserWarning,
                )

        # Fallback to general min/max if needed
        if (min_value is None or max_value is None) and isinstance(current_range_source, dict):
            if min_value is None:
                min_value = current_range_source.get("min")
            if max_value is None:
                max_value = current_range_source.get("max")

        if age_dependent:
            warnings.warn(f"Age dependent normal range not implemented yet for LabValue '{self.name}'. Age: {age}.")

        if special_case:
            warnings.warn(f"Special case normal range not implemented yet for LabValue '{self.name}'.")

        # Final contextual warning
        if min_value is None and max_value is None:
            # Do not warn here; let get_normal_value handle the warning for missing range
            return {"min": min_value, "max": max_value}
        if min_value is None:
            context_parts = []
            if gender_dependent:
                gender_repr = gender.name if gender and hasattr(gender, "name") else "None"
                if gender_name_to_use and gender_name_to_use != gender_repr:
                    gender_repr = f"{gender_repr} (lookup attempted for: {gender_name_to_use})"
                context_parts.append(f"gender: {gender_repr}")
            if age_dependent:
                context_parts.append(f"age: {age}")

            warning_message = f"Could not determine a 'min' normal range for LabValue '{self.name}'"
            if context_parts:
                warning_message += f" with context ({', '.join(context_parts)})."
            else:
                warning_message += " (general context)."
            warning_message += " Check LabValue's default_normal_range definition."
            warnings.warn(warning_message, UserWarning)

        return {"min": min_value, "max": max_value}

    def get_increased_value(self, patient: Optional["Patient"] = None):  # -> Any | None:
        """
        Returns a value that is considered increased for this lab value.
        It prioritizes sampling from a numerical distribution if available,
        otherwise uses the upper bound of the normal range.
        """
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        upper_bound = normal_range.get("max")

        if self.default_numerical_value_distribution:
            if patient:
                # Attempt to sample above the upper bound, or a high value if no bound
                for _ in range(10):  # Try a few times to get a value if bounds are restrictive
                    generated_value = self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
                    if upper_bound is not None:
                        if generated_value > upper_bound:
                            return generated_value
                    # Heuristic for "high" if no upper_bound, compare against mean + stddev
                    elif (
                        hasattr(self.default_numerical_value_distribution, "mean")
                        and hasattr(self.default_numerical_value_distribution, "stddev")
                        and self.default_numerical_value_distribution.mean is not None
                        and self.default_numerical_value_distribution.stddev is not None
                        and generated_value > (self.default_numerical_value_distribution.mean + self.default_numerical_value_distribution.stddev)
                    ):
                        return generated_value
                # Fallback if sampling fails to produce a clearly increased value
                if upper_bound is not None:
                    return upper_bound + (abs(upper_bound * self.bound_adjustment_factor) if upper_bound != 0 else 1)  # Increase by factor or 1
                # If no upper bound and sampling didn't provide a clear high value, return a generated value as last resort
                return self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
            else:  # No patient, cannot use distribution
                warnings.warn(
                    f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for increased value."
                )
                if upper_bound is not None:
                    return upper_bound + (abs(upper_bound * self.bound_adjustment_factor) if upper_bound != 0 else 1)
                else:
                    warnings.warn(f"Cannot determine an increased value for {self.name} without an upper normal range or patient context for distribution.")
                    return None

        elif upper_bound is not None:
            return upper_bound + (abs(upper_bound * self.bound_adjustment_factor) if upper_bound != 0 else 1)
        else:
            warnings.warn(f"Cannot determine an increased value for {self.name} without a numerical distribution or an upper normal range.")
            return None

    def get_normal_value(self, patient: Optional["Patient"] = None):
        """
        Returns a value considered normal for this lab value.

        If a numerical distribution and patient context are available, attempts to generate a value within the normal range. Falls back to the midpoint of the normal range or to available bounds if sampling fails or context is insufficient. Returns None if neither a normal range nor a distribution is available.
        """
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        lower_bound = normal_range.get("min")
        upper_bound = normal_range.get("max")

        if self.default_numerical_value_distribution:
            if patient:
                for _ in range(10):  # Try a few times
                    generated_value = self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
                    if lower_bound is not None and upper_bound is not None:
                        if lower_bound <= generated_value <= upper_bound:
                            return generated_value
                    elif lower_bound is not None and generated_value >= lower_bound:  # No upper bound
                        return generated_value
                    elif upper_bound is not None and generated_value <= upper_bound:  # No lower bound
                        return generated_value
                    elif lower_bound is None and upper_bound is None:  # No range defined
                        return generated_value
                # Fallback if sampling fails to produce a value in range
                if lower_bound is not None and upper_bound is not None:
                    return (lower_bound + upper_bound) / 2.0
                # Return any generated value as last resort
                return self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
            else:  # No patient, cannot use distribution
                warnings.warn(
                    f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for normal value."
                )
                if lower_bound is not None and upper_bound is not None:
                    return (lower_bound + upper_bound) / 2.0
                elif lower_bound is not None:
                    return lower_bound
                elif upper_bound is not None:
                    return upper_bound
                else:
                    warnings.warn(f"Cannot determine a normal value for {self.name} without a normal range or patient context for distribution.", UserWarning)
                    return None

        elif lower_bound is not None and upper_bound is not None:
            return (lower_bound + upper_bound) / 2.0
        elif lower_bound is not None:  # Only min is defined
            return lower_bound
        elif upper_bound is not None:  # Only max is defined
            return upper_bound
        else:
            warnings.warn(f"Cannot determine a normal value for {self.name} without a numerical distribution or a normal range.")
            return None

    def get_decreased_value(self, patient: Optional["Patient"] = None):
        """
        Returns a value that is considered decreased for this lab value.
        It prioritizes sampling from a numerical distribution if available,
        otherwise uses the lower bound of the normal range.
        """
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        lower_bound = normal_range.get("min")

        if self.default_numerical_value_distribution:
            if patient:
                for _ in range(10):  # Try a few times
                    generated_value = self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
                    if lower_bound is not None:
                        if generated_value < lower_bound:
                            return generated_value
                    # Heuristic for "low" if no lower_bound, compare against mean - stddev
                    elif (
                        hasattr(self.default_numerical_value_distribution, "mean")
                        and hasattr(self.default_numerical_value_distribution, "stddev")
                        and self.default_numerical_value_distribution.mean is not None
                        and self.default_numerical_value_distribution.stddev is not None
                        and generated_value < (self.default_numerical_value_distribution.mean - self.default_numerical_value_distribution.stddev)
                    ):
                        return generated_value
                # Fallback
                if lower_bound is not None:
                    return lower_bound - (abs(lower_bound * self.bound_adjustment_factor) if lower_bound != 0 else 1)  # Decrease by factor or 1
                # Return any generated value as last resort
                return self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
            else:  # No patient, cannot use distribution
                warnings.warn(
                    f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for decreased value."
                )
                if lower_bound is not None:
                    return lower_bound - (abs(lower_bound * self.bound_adjustment_factor) if lower_bound != 0 else 1)
                else:
                    warnings.warn(f"Cannot determine a decreased value for {self.name} without a lower normal range or patient context for distribution.")
                    return None

        elif lower_bound is not None:
            return lower_bound - (abs(lower_bound * self.bound_adjustment_factor) if lower_bound != 0 else 1)
        else:
            warnings.warn(f"Cannot determine a decreased value for {self.name} without a numerical distribution or a lower normal range.")
            return None
