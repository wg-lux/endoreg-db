from django.db import models
from typing import List


class DiseaseManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a model instance by its natural key.
        
        Args:
            name: The natural key value used to match the model's 'name' field.
        
        Returns:
            The model instance corresponding to the provided natural key.
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
        Return the natural key for this model instance.
        
        The natural key is defined as a tuple containing the instance's name.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the instance using its name attribute.
        """
        return str(self.name)

    def get_classifications(self) -> List["DiseaseClassification"]:
        """
        Retrieves all classifications associated with this disease.
        
        Returns:
            List[DiseaseClassification]: A list of related disease classification objects.
        """
        classifications: List[DiseaseClassification] = [
            _ for _ in self.disease_classifications.all()
        ]
        return classifications


class DiseaseClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.
        
        Args:
            name: A unique identifier representing the natural key of the instance.
        
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
        Return the natural key for the instance.
        
        Returns a single-element tuple containing the object's name, which is used as a
        unique identifier for natural key-based lookups.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the model instance.
        
        This method converts the instance's name attribute to a string, ensuring a clear
        and consistent display across Django interfaces.
        """
        return str(self.name)

    def get_choices(self) -> List["DiseaseClassificationChoice"]:
        """
        Retrieves all choices associated with this disease classification.
        
        Returns:
            List[DiseaseClassificationChoice]: A list of related disease classification choices.
        """
        choices: List[DiseaseClassificationChoice] = [
            _ for _ in self.disease_classification_choices.all()
        ]
        return choices


class DiseaseClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """Retrieve an object by its natural key.
        
        Queries for and returns the instance whose 'name' attribute matches the provided key.
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
        Return a tuple representing the natural key for the instance.
        
        The tuple contains the unique name of the model instance, which is used to
        identify it naturally.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the string representation of the object's name.
        
        This method converts the model's 'name' attribute to a string for a human-readable display.
        """
        return str(self.name)
