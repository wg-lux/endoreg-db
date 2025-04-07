from django.db import models
from typing import List


class DiseaseManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance by its natural key.
        
        This method returns the instance whose 'name' attribute matches the provided key.
        
        Args:
            name: The unique identifier used as the natural key.
        
        Returns:
            The model instance corresponding to the given natural key.
        """
        return self.get(name=name)


class Disease(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    objects = DiseaseManager()

    def natural_key(self):
        """
        Return the natural key for the model instance.
        
        The natural key is defined as a tuple containing the instance's unique name.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        Converts and returns the instance's name attribute as a string.
        """
        return str(self.name)

    def get_classifications(self) -> List["DiseaseClassification"]:
        """
        Return all associated DiseaseClassification instances.
        
        Retrieves all DiseaseClassification objects related to this disease from the
        'disease_classifications' manager and returns them as a list.
        
        Returns:
            List[DiseaseClassification]: A list of associated disease classifications.
        """
        classifications: List[DiseaseClassification] = [
            _ for _ in self.disease_classifications.all()
        ]
        return classifications


class DiseaseClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance by its natural key.
        
        Args:
            name (str): The unique natural key corresponding to the model's name.
        
        Returns:
            The model instance with the matching name.
        """
        return self.get(name=name)


class DiseaseClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease = models.ForeignKey(
        Disease, on_delete=models.CASCADE, related_name="disease_classifications"
    )

    objects = DiseaseClassificationManager()

    def natural_key(self):
        """
        Returns the natural key for the instance.
        
        The natural key is represented as a tuple containing the unique name of the object.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the name.
        
        Returns:
            str: The name attribute converted to a string.
        """
        return str(self.name)

    def get_choices(self) -> List["DiseaseClassificationChoice"]:
        """
        Returns the list of associated DiseaseClassificationChoice instances.
        """
        choices: List[DiseaseClassificationChoice] = [
            _ for _ in self.disease_classification_choices.all()
        ]
        return choices


class DiseaseClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance using its natural key.
        
        Args:
            name: The unique identifier for the model instance.
        
        Returns:
            The model instance with a matching name.
        """
        return self.get(name=name)


class DiseaseClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease_classification = models.ForeignKey(
        DiseaseClassification,
        on_delete=models.CASCADE,
        related_name="disease_classification_choices",
    )

    objects = DiseaseClassificationChoiceManager()

    def natural_key(self):
        """Return the object's natural key.
        
        Returns a single-element tuple containing the unique name attribute, which is used
        for natural key serialization and lookup.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        This method converts the instance's name attribute to a string,
        providing a human-readable representation of the object.
        """
        return str(self.name)
