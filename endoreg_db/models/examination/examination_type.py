from django.db import models


class ExaminationTypeManager(models.Manager):
    """
    Manager for ExaminationType with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "ExaminationType":
        """
        Retrieves an ExaminationType instance using its natural key.
        
        Args:
            name: The natural identifier for the ExaminationType, typically the unique name.
        
        Returns:
            The ExaminationType instance that matches the given name.
        """
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
        Return the string representation of the examination type.
        
        If an English name is provided, it is returned; otherwise, the default name is used.
        """
        name = self.name_en or self.name
        name = str(name)
        return name

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the examination type.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)
