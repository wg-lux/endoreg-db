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
        
        This method returns the default normal range when the lab value is not age-, gender-, or special-case dependent.
        For gender-dependent ranges, it uses a gender-specific default range and, if no gender is provided, selects one at random with a warning.
        Note that age-dependent and special-case normal ranges are not implemented and will issue warnings when invoked.
        
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

        if not age_dependent and not gender_dependent and not special_case:
            min_value = self.default_normal_range.get("min", None)
            max_value = self.default_normal_range.get("max", None)

        if age_dependent:
            # get normal range for age)
            warnings.warn("Age dependent normal range not implemented yet")
            pass

        if gender_dependent:
            if not gender:
                warnings.warn(
                    "Calling get_normal_range with gender_dependent=True requires gender to be set, choosing by random"
                )
                # set gender to either "male" or "female"
                from random import choice

                choices = ["male", "female"]
                gender = choice(choices)

            default_range_dict = self.default_normal_range.get(gender.name, {})
            min_value = default_range_dict.get("min", None)
            max_value = default_range_dict.get("max", None)

        if special_case:
            # get normal range for special case
            warnings.warn("Special case normal range not implemented yet")

        normal_range_dict = {"min": min_value, "max": max_value}

        return normal_range_dict
