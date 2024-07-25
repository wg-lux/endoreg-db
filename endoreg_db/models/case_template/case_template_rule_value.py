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
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    distribution_type = models.CharField(max_length=255, choices=DISTRIBUTION_TYPES)


    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

class CaseTemplateRuleValueManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplateRuleValue(models.Model):
    """
    A class representing a case template rule value.

    Attributes:
        value (str): The value of the case template rule value.
        case_template_rule (CaseTemplateRule): The case template rule of the case template rule value.
        case_template (CaseTemplate): The case template of the case template rule value.
    """
    objects = CaseTemplateRuleValueManager()

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    fk_value = models.CharField(max_length=255, null=True, blank=True)
    numeric_value = models.FloatField(null=True, blank=True)
    text_value = models.CharField(max_length=255, null=True, blank=True)

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_value(self):
        if self.fk_value:
            return self.fk_value
        elif self.numeric_value:
            return self.numeric_value
        elif self.text_value:
            return self.text_value
        else:
            return None
