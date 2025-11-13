from typing import TYPE_CHECKING, List

from django.db import models

if TYPE_CHECKING:
    from .patient import PatientDisease


class DiseaseManager(models.Manager):
    """Manager for Disease with natural key support."""

    def get_by_natural_key(self, name):
        """
        Retrieve a Disease instance by its natural key (name).

        Args:
            name: The natural key value used to match the model's 'name' field.

        Returns:
            The Disease instance corresponding to the provided natural key.
        """
        return self.get(name=name)


class Disease(models.Model):
    """
    Represents a specific disease or medical condition.

    Can define associated subcategories and numerical descriptors applicable to the disease itself.
    """

    name = models.CharField(max_length=255, unique=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

    objects = DiseaseManager()

    if TYPE_CHECKING:

        @property
        def disease_classifications(self) -> models.QuerySet["DiseaseClassification"]: ...

        @property
        def patient_diseases(self) -> models.QuerySet["PatientDisease"]: ...

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the disease."""
        return str(self.name)

    def get_classifications(self) -> List["DiseaseClassification"]:
        """
        Retrieves all classification systems associated with this disease.

        Returns:
            List[DiseaseClassification]: A list of related disease classification objects.
        """
        classifications: List[DiseaseClassification] = [_ for _ in self.disease_classifications.all()]
        return classifications


class DiseaseClassificationManager(models.Manager):
    """Manager for DiseaseClassification with natural key support."""

    def get_by_natural_key(self, name):
        """
        Retrieve a DiseaseClassification instance by its natural key (name).

        Args:
            name: A unique identifier representing the natural key of the instance.

        Returns:
            The DiseaseClassification instance corresponding to the given natural key.
        """
        return self.get(name=name)


class DiseaseClassification(models.Model):
    """
    Represents a classification system applicable to a specific disease (e.g., Forrest classification for ulcers).
    """

    name = models.CharField(max_length=255, unique=True)

    disease = models.ForeignKey(Disease, on_delete=models.CASCADE, related_name="disease_classifications")

    objects = DiseaseClassificationManager()

    if TYPE_CHECKING:
        disease: models.ForeignKey["Disease"]

        @property
        def disease_classification_choices(self) -> models.manager.RelatedManager["DiseaseClassificationChoice"]: ...

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the classification."""
        return str(self.name)

    def get_choices(self) -> List["DiseaseClassificationChoice"]:
        """
        Retrieves all choices within this classification system.

        Returns:
            List[DiseaseClassificationChoice]: A list of related disease classification choices.
        """
        choices: List[DiseaseClassificationChoice] = [_ for _ in self.disease_classification_choices.all()]
        return choices


class DiseaseClassificationChoiceManager(models.Manager):
    """Manager for DiseaseClassificationChoice with natural key support."""

    def get_by_natural_key(self, name):
        """
        Retrieve a DiseaseClassificationChoice instance by its natural key (name).

        Queries for and returns the instance whose 'name' attribute matches the provided key.

        Args:
            name: The natural key value used to match the model's 'name' field.

        Returns:
            The DiseaseClassificationChoice instance corresponding to the provided natural key.
        """
        return self.get(name=name)


class DiseaseClassificationChoice(models.Model):
    """
    Represents a specific choice within a disease classification system (e.g., Forrest IIa).
    """

    name = models.CharField(max_length=255, unique=True)

    disease_classification = models.ForeignKey(
        DiseaseClassification,
        on_delete=models.CASCADE,
        related_name="disease_classification_choices",
    )

    objects = DiseaseClassificationChoiceManager()

    if TYPE_CHECKING:
        disease_classification: models.ForeignKey["DiseaseClassification"]

        @property
        def patient_diseases(self) -> models.manager.RelatedManager["PatientDisease"]: ...

    def natural_key(self):
        """Returns the natural key (name) as a tuple."""
        return (self.name,)

    def __str__(self):
        """Returns the name of the classification choice."""
        return str(self.name)
