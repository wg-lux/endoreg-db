from django.db import models
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.models import InformationSourceType

def get_prediction_information_source():
    """
    Retrieve the prediction information source.
    
    This function queries the InformationSource model for an entry with the name
    "prediction" using Django's ORM and asserts that the retrieved object is valid.
    It returns the matching InformationSource instance.
    
    Returns:
        InformationSource: The InformationSource object with name "prediction".
    
    Raises:
        AssertionError: If no prediction information source is found.
    """
    _source = InformationSource.objects.get(name="prediction")

    # make sure to return only one object
    assert _source, "No prediction information source found"
    return _source


class InformationSourceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name: The natural key value corresponding to the model's 'name' field.
        
        Returns:
            The model instance that matches the provided natural key.
        """
        return self.get(name=name)


class InformationSource(models.Model):
    objects = InformationSourceManager()

    name = models.CharField(max_length=100)

    url = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    date_created = models.DateField(auto_now_add=True)
    date_modified = models.DateField(auto_now=True)
    abbreviation = models.CharField(max_length=100, blank=True, null=True, unique=True)
    
    if TYPE_CHECKING:
        information_source_types: models.QuerySet["InformationSourceType"]
    class Meta:
        verbose_name = "Information Source"
        verbose_name_plural = "Information Sources"

        # add name and abbreviation as index
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["abbreviation"]),
        ] 


    def natural_key(self):
        """
        Returns the natural key tuple for the information source.
        
        The tuple contains the object's name, which uniquely identifies it for 
        serialization and natural key lookup.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the InformationSource instance.
        
        This method returns the instance's name attribute converted explicitly to a string.
        """
        return str(self.name)

class InformationSourceTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name: The natural key value corresponding to the model's 'name' field.
        
        Returns:
            The model instance that matches the provided natural key.
        """
        return self.get(name=name)
    
class InformationSourceType(models.Model):
    objects = InformationSourceTypeManager()

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    information_sources = models.ManyToManyField(
        InformationSource,
        related_name="information_source_types",
        blank=True,
    )

    class Meta:
        verbose_name = "Information Source Type"
        verbose_name_plural = "Information Source Types"

    # information_sources: models.QuerySet["InformationSource"]

    @classmethod
    def get_prediction_type(cls) -> "InformationSourceType":
        """
        Returns the 'prediction' information source type.

        Raises:
            cls.DoesNotExist: If the 'prediction' type is not found.
        """
        try:
            return cls.objects.get(name="prediction")
        except cls.DoesNotExist as e:
            raise cls.DoesNotExist(
                "The 'prediction' InformationSourceType was not found. "
                "Please check your data fixtures or initial data migrations."
            ) from e

    @classmethod
    def get_manual_annotation_type(cls) -> "InformationSourceType":
        """
        Returns the 'manual_annotation' information source type.

        Raises:
            cls.DoesNotExist: If the 'manual_annotation' type is not found.
        """
        try:
            return cls.objects.get(name="manual_annotation")
        except cls.DoesNotExist as e:
            raise cls.DoesNotExist(
                "The 'manual_annotation' InformationSourceType was not found. "
                "Please check your data fixtures or initial data migrations."
            ) from e

    def natural_key(self):
        """
        Returns the natural key tuple for the information source type.
        
        The tuple contains the object's name, which uniquely identifies it for 
        serialization and natural key lookup.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the InformationSourceType instance.
        
        This method returns the instance's name attribute converted explicitly to a string.
        """
        return str(self.name)