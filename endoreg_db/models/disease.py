from django.db import models
from typing import List


class DiseaseManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name: The natural key value (typically the instance's name) to look up.
        
        Returns:
            The model instance that matches the given natural key.
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
        Return a tuple containing the natural key of this instance.
        
        The natural key is defined solely by the instance's name attribute.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance's name.
        
        Returns:
            str: The string representation of the instance's name.
        """
        return str(self.name)

    def get_classifications(self) -> List["DiseaseClassification"]:
        """
        Return all related disease classification instances.
        
        Retrieves a list of disease classifications linked to this disease.
        """
        classifications: List[DiseaseClassification] = [
            _ for _ in self.disease_classifications.all()
        ]
        return classifications


class DiseaseClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance by its natural key.
        
        Args:
            name (str): The unique name identifying the instance.
        
        Returns:
            The model instance corresponding to the given natural key.
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
        Returns the natural key for the model instance.
        
        The natural key is represented as a tuple containing the instance's unique name,
        which is used by Django's serialization framework.
        """
        return (self.name,)

    def __str__(self):
        """Return the instance's name as a string.
        
        Converts the object's `name` attribute to a string, ensuring a consistent textual
        representation for display purposes.
        """
        return str(self.name)

    def get_choices(self) -> List["DiseaseClassificationChoice"]:
        """
        Return all choices associated with this disease classification.
        
        Retrieves all related DiseaseClassificationChoice instances from the reverse
        relation and returns them as a list.
        """
        choices: List[DiseaseClassificationChoice] = [
            _ for _ in self.disease_classification_choices.all()
        ]
        return choices


class DiseaseClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance by its natural key.
        
        Args:
            name: The unique identifier (typically the model's name) used for lookup.
        
        Returns:
            The model instance that matches the provided natural key.
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
        """
        Returns the natural key of the instance.
        
        The natural key is defined as a tuple containing the unique name attribute.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance.
        
        Converts the instance's 'name' attribute to a string to ensure a consistent
        representation.
        """
        return str(self.name)
