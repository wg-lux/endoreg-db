from django.db import models
import warnings

LANG = "de"

class LabValueManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class LabValue(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    abbreviation = models.CharField(max_length=10, blank=True, null=True)
    default_unit = models.ForeignKey('Unit', on_delete=models.CASCADE, blank=True, null=True)
    numeric_precision = models.IntegerField(default=3)
    default_single_categorical_value_distribution = models.ForeignKey(
        'SingleCategoricalValueDistribution',
        on_delete=models.CASCADE,
        blank=True, null=True,
        related_name='default_single_categorical_value_distribution'
    )
    default_numerical_value_distribution = models.ForeignKey(
        'NumericValueDistribution',
        on_delete=models.CASCADE,
        blank=True, null=True,
        related_name='default_numerical_value_distribution'
    )
    default_multiple_categorical_value_distribution = models.ForeignKey(
        'MultipleCategoricalValueDistribution',
        on_delete=models.CASCADE,
        blank=True, null=True,
        related_name='default_multiple_categorical_value_distribution'
    )
    default_date_value_distribution = models.ForeignKey(
        'DateValueDistribution',
        on_delete=models.CASCADE,
        blank=True, null=True,
        related_name='default_date_value_distribution'
    )
    default_normal_range = models.JSONField(blank=True, null=True)
    normal_range_age_dependent = models.BooleanField(default=False)
    normal_range_gender_dependent = models.BooleanField(default=False)
    normal_range_special_case = models.BooleanField(default=False)
    objects = LabValueManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
    def get_default_default_distribution(self):
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
    
    def get_normal_range(self, age=None, gender=None):
        age_dependent = self.normal_range_age_dependent
        gender_dependent = self.normal_range_gender_dependent
        special_case = self.normal_range_special_case

        min_value = None
        max_value = None

        if not age_dependent and \
        not gender_dependent and \
        not special_case:
            min_value = self.default_normal_range.get('min', None)
            max_value = self.default_normal_range.get('max', None)

        if age_dependent: 
            # get normal range for age)
            warnings.warn("Age dependent normal range not implemented yet")
            pass
        
        if gender_dependent:
            if not gender:
                warnings.warn("Calling get_normal_range with gender_dependent=True requires gender to be set, choosing by random")
                # set gender to either "male" or "female"
                from random import choice
                choices = ["male", "female"]
                gender = choice(choices)

            default_range_dict = self.default_normal_range.get(gender, {})
            min_value = default_range_dict.get('min', None)
            max_value = default_range_dict.get('max', None)

        if special_case:
            # get normal range for special case
            warnings.warn("Special case normal range not implemented yet")

        return min_value, max_value

