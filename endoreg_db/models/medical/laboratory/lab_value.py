from django.db import models
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.other.unit import Unit
    from ...other.distribution import (
        SingleCategoricalValueDistribution,
        NumericValueDistribution,
        MultipleCategoricalValueDistribution,
        DateValueDistribution,
    )
    from ...administration.person.patient import Patient  # Added Patient for type hinting

LANG = "de"


class LabValueManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance by its natural key.

        This method returns the instance whose unique name matches the provided natural key.

        Args:
            name: The unique identifier corresponding to the model's "name" field.

        Returns:
            The model instance with a matching name.
        """
        return self.get(name=name)


class LabValue(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    abbreviation = models.CharField(max_length=10, blank=True, null=True)
    default_unit = models.ForeignKey(
        "Unit", on_delete=models.CASCADE, blank=True, null=True
    )
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
    objects = LabValueManager()

    if TYPE_CHECKING:
        default_unit: "Unit"
        default_single_categorical_value_distribution: "SingleCategoricalValueDistribution"
        default_numerical_value_distribution: "NumericValueDistribution"
        default_multiple_categorical_value_distribution: "MultipleCategoricalValueDistribution"
        default_date_value_distribution: "DateValueDistribution"

    def natural_key(self):
        """
        Return a tuple representing the natural key for this instance.

        Returns:
            tuple: A single-element tuple containing the instance's unique name.
        """
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

    def get_normal_range(self, age: int = None, gender=None):
        """
        Retrieve the normal range for the lab value based on optional age and gender.

        This method prioritizes gender-specific ranges if the lab value is gender-dependent.
        If a gender-specific range (or its 'min' value) is not found, it falls back to
        a general default normal range if defined at the top level of default_normal_range.
        Age-dependent and special-case normal ranges are not fully implemented and will issue warnings.

        Args:
            age (int, optional): The age in years used for age-dependent range computation.
            gender (Gender, optional): The gender instance used for gender-dependent range computation.

        Returns:
            dict: A dictionary with 'min' and 'max' keys indicating the lower and upper bounds of the normal range.
        """
        from ...other.gender import Gender

        assert isinstance(age, int) or age is None
        assert isinstance(gender, Gender) or gender is None

        age_dependent = self.normal_range_age_dependent
        gender_dependent = self.normal_range_gender_dependent
        special_case = self.normal_range_special_case

        min_value = None
        max_value = None
        
        gender_name_to_use = None
        if gender_dependent:
            if gender and hasattr(gender, 'name') and gender.name:
                gender_name_to_use = gender.name
            elif not gender: # Gender not provided for a gender-dependent value
                warnings.warn(
                    f"Gender not provided for gender-dependent LabValue '{self.name}'. Choosing a random gender (male/female) for normal range lookup."
                )
                from random import choice
                gender_name_to_use = choice(["male", "female"])
            # If gender object is present but gender.name is 'unknown', gender_name_to_use will be 'unknown'.
            # If gender object is present but .name is None/empty, gender_name_to_use remains None.

        # Try to get gender-specific range if gender_dependent and gender_name_to_use is determined
        if gender_name_to_use: # This implies gender_dependent is True
            if self.default_normal_range and isinstance(self.default_normal_range, dict):
                gender_specific_data = self.default_normal_range.get(gender_name_to_use)
                if gender_specific_data and isinstance(gender_specific_data, dict):
                    min_value = gender_specific_data.get("min")
                    max_value = gender_specific_data.get("max")
                else:
                    warnings.warn(
                        f"No normal range data found for gender '{gender_name_to_use}' for LabValue '{self.name}'. "
                        f"Attempting to use general default normal range if available."
                    )
            else:
                warnings.warn(
                    f"default_normal_range is not a valid dictionary for LabValue '{self.name}' when looking up gender '{gender_name_to_use}'. "
                    f"Attempting to use general default normal range if available."
                )
        
        # If min_value is still None (either not gender_dependent, or gender_dependent but specific range not found/incomplete),
        # try to use the general default normal range.
        if min_value is None and self.default_normal_range and isinstance(self.default_normal_range, dict):
            general_min = self.default_normal_range.get("min")
            if general_min is not None:
                min_value = general_min
            
            # Set max_value from general default only if it hasn't been set by a (potentially partial) gender-specific range
            if max_value is None: 
                general_max = self.default_normal_range.get("max")
                if general_max is not None:
                    max_value = general_max
        
        # Age-dependent and special_case logic (currently just warnings)
        if age_dependent:
            warnings.warn(f"Age dependent normal range not implemented yet for LabValue '{self.name}'. Age: {age}.")
        
        if special_case:
            warnings.warn(f"Special case normal range not implemented yet for LabValue '{self.name}'.")

        # Final check and warning if min_value is still None
        if min_value is None:
            context_parts = []
            if gender_dependent:
                gender_repr = (gender.name if gender and hasattr(gender, 'name') else 'None')
                if gender_name_to_use and gender_name_to_use != gender_repr : # e.g. random choice was made
                     gender_repr = f"{gender_repr} (lookup attempted for: {gender_name_to_use})"
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
            warnings.warn(warning_message)
            
        return {"min": min_value, "max": max_value}

    def get_increased_value(self, patient: "Patient" = None):
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
                    generated_value = self.default_numerical_value_distribution.generate_value(
                        lab_value=self, patient=patient
                    )
                    if upper_bound is not None:
                        if generated_value > upper_bound:
                            return generated_value
                    # Heuristic for "high" if no upper_bound, compare against mean + stddev
                    elif hasattr(self.default_numerical_value_distribution, "mean") and \
                            hasattr(self.default_numerical_value_distribution, "stddev") and \
                            self.default_numerical_value_distribution.mean is not None and \
                            self.default_numerical_value_distribution.stddev is not None and \
                            generated_value > (
                            self.default_numerical_value_distribution.mean + self.default_numerical_value_distribution.stddev
                    ):
                        return generated_value
                # Fallback if sampling fails to produce a clearly increased value
                if upper_bound is not None:
                    return upper_bound + (abs(upper_bound * 0.1) if upper_bound != 0 else 1)  # Increase by 10% or 1
                # If no upper bound and sampling didn't provide a clear high value, return a generated value as last resort
                return self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
            else:  # No patient, cannot use distribution
                warnings.warn(
                    f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for increased value."
                )
                if upper_bound is not None:
                    return upper_bound + (abs(upper_bound * 0.1) if upper_bound != 0 else 1)
                else:
                    warnings.warn(
                        f"Cannot determine an increased value for {self.name} without an upper normal range or patient context for distribution."
                    )
                    return None

        elif upper_bound is not None:
            return upper_bound + (abs(upper_bound * 0.1) if upper_bound != 0 else 1)
        else:
            warnings.warn(
                f"Cannot determine an increased value for {self.name} without a numerical distribution or an upper normal range."
            )
            return None

    def get_normal_value(self, patient: "Patient" = None):
        """
        Returns a value that is considered normal for this lab value.
        It prioritizes sampling from a numerical distribution if available and
        the sampled value falls within the normal range. Otherwise, it uses
        the midpoint of the normal range or a direct sample.
        """
        _age = patient.age() if patient else None
        _gender = patient.gender if patient else None
        normal_range = self.get_normal_range(age=_age, gender=_gender)
        lower_bound = normal_range.get("min")
        upper_bound = normal_range.get("max")

        if self.default_numerical_value_distribution:
            if patient:
                for _ in range(10):  # Try a few times
                    generated_value = self.default_numerical_value_distribution.generate_value(
                        lab_value=self, patient=patient
                    )
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
                    warnings.warn(
                        f"Cannot determine a normal value for {self.name} without a normal range or patient context for distribution."
                    )
                    return None

        elif lower_bound is not None and upper_bound is not None:
            return (lower_bound + upper_bound) / 2.0
        elif lower_bound is not None:  # Only min is defined
            return lower_bound
        elif upper_bound is not None:  # Only max is defined
            return upper_bound
        else:
            warnings.warn(
                f"Cannot determine a normal value for {self.name} without a numerical distribution or a normal range."
            )
            return None

    def get_decreased_value(self, patient: "Patient" = None):
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
                    generated_value = self.default_numerical_value_distribution.generate_value(
                        lab_value=self, patient=patient
                    )
                    if lower_bound is not None:
                        if generated_value < lower_bound:
                            return generated_value
                    # Heuristic for "low" if no lower_bound, compare against mean - stddev
                    elif hasattr(self.default_numerical_value_distribution, "mean") and \
                            hasattr(self.default_numerical_value_distribution, "stddev") and \
                            self.default_numerical_value_distribution.mean is not None and \
                            self.default_numerical_value_distribution.stddev is not None and \
                            generated_value < (
                            self.default_numerical_value_distribution.mean - self.default_numerical_value_distribution.stddev
                    ):
                        return generated_value
                # Fallback
                if lower_bound is not None:
                    return lower_bound - (abs(lower_bound * 0.1) if lower_bound != 0 else 1)  # Decrease by 10% or 1
                # Return any generated value as last resort
                return self.default_numerical_value_distribution.generate_value(lab_value=self, patient=patient)
            else:  # No patient, cannot use distribution
                warnings.warn(
                    f"Cannot use numerical distribution for {self.name} without patient context. Falling back to normal range logic for decreased value."
                )
                if lower_bound is not None:
                    return lower_bound - (abs(lower_bound * 0.1) if lower_bound != 0 else 1)
                else:
                    warnings.warn(
                        f"Cannot determine a decreased value for {self.name} without a lower normal range or patient context for distribution."
                    )
                    return None

        elif lower_bound is not None:
            return lower_bound - (abs(lower_bound * 0.1) if lower_bound != 0 else 1)
        else:
            warnings.warn(
                f"Cannot determine a decreased value for {self.name} without a numerical distribution or a lower normal range."
            )
            return None
