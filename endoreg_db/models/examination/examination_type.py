from django.db import models

class ExaminationTypeManager(models.Manager):
    """
    Manager for ExaminationType with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "ExaminationType":
        return self.get(name=name)
    
class ExaminationType(models.Model):
    """
    Represents a type of examination.

    Attributes:
        name (str): The unique name of the examination type.
        name_de (str): The German name of the examination type.
        name_en (str): The English name of the examination type.
    """
    objects = ExaminationTypeManager()
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self) -> str:
        """
        String representation of the examination type.

        Returns:
            str: The name of the examination type.
        """
        return self.name_en or self.name

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the examination type.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)
