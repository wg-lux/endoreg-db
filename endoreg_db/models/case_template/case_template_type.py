from django.db import models


class CaseTemplateTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class CaseTemplateType(models.Model):
    """
    A class representing a case template type.

    Attributes:
        name (str): The name of the case template type.
        description (str): A description of the case template type.

    """
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    objects = CaseTemplateTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name