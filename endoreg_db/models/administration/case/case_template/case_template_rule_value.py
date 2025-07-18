from django.db import models

class CaseTemplateRuleValueTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class CaseTemplateRuleValueType(models.Model):
    """
    A class representing a case template rule value type.

    Attributes:
        name (str): The name of the case template rule value type.
        distribution_type: One of "single_categorical", "multiple_categorical", "numeric"
        description (str): A description of the case template rule value type.
    """
    DISTRIBUTION_TYPES = [
        ("single_categorical", "single_categorical"),
        ("multiple_categorical", "multiple_categorical"),
        ("numeric", "numeric")
    ]
    objects = CaseTemplateRuleValueTypeManager()

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    distribution_type = models.CharField(max_length=255, choices=DISTRIBUTION_TYPES)


    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

class CaseTemplateRuleValueManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplateRuleValue(models.Model):
    """
    Represents a case template rule value.

    Attributes:
        name (str): The name of the rule value.
        description (str): A description of the rule value.
        fk_value (str): Foreign key value stored as a string.
        numeric_value (float): Numeric value for the rule.
        text_value (str): Text value for the rule.
    """
    objects = CaseTemplateRuleValueManager()

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    fk_value = models.CharField(max_length=255, null=True, blank=True)
    numeric_value = models.FloatField(null=True, blank=True)
    text_value = models.CharField(max_length=255, null=True, blank=True)

    def natural_key(self):
        """
        Returns the natural key for the object.
        """
        return (self.name,)
    
    def __str__(self):
        """
        String representation of the object.
        """
        return str(self.name)
    
    def get_value(self):
        """
        Retrieves the value based on priority:
        - fk_value > numeric_value > text_value > None.
        
        Returns:
            str | float | None: The value based on the type hierarchy.
        """
        if self.fk_value:
            return self.fk_value
        elif self.numeric_value:
            return self.numeric_value
        elif self.text_value:
            return self.text_value
        else:
            return None
