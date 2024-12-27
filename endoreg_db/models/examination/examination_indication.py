from django.db import models
from typing import List

class ExaminationIndicationManager(models.Manager):
    """
    Manager for ExaminationIndication with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "ExaminationIndication":
        return self.get(name=name)
    
class ExaminationIndication(models.Model):
    """
    Represents an indication for an examination.

    Attributes:
        name (str): The unique name of the indication.
        name_de (str): The German name of the indication.
        name_en (str): The English name of the indication.
        classification (ForeignKey): The classification associated with the indication.
        examination (ForeignKey): The examination associated with the indication.
    """
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    classification = models.ForeignKey(
        'ExaminationIndicationClassification', on_delete=models.CASCADE,
        related_name='indications',
        blank=True, null=True
    )
    examination = models.ForeignKey(
        'Examination', on_delete=models.CASCADE,
        related_name='indications',
    )
    
    objects = ExaminationIndicationManager()
    
    def natural_key(self) -> tuple:
        """
        Returns the natural key for the indication.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    def __str__(self) -> str:
        """
        String representation of the indication.

        Returns:
            str: The name of the indication.
        """
        return self.name

    def get_choices(self) -> List['ExaminationIndicationClassificationChoice']:
        """
        Retrieves all choices associated with the classification.

        Returns:
            list: A list of classification choices.
        """
        return list(self.classification.choices.all()) if self.classification else []

    def get_examination(self) -> 'Examination':
        """
        Retrieves the associated examination.

        Returns:
            Examination: The associated examination.
        """
        return self.examination

class ExaminationIndicationClassificationManager(models.Manager):
    """
    Manager for ExaminationIndicationClassification with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "ExaminationIndicationClassification":
        return self.get(name=name)

class ExaminationIndicationClassification(models.Model):
    """
    Represents a classification for examination indications.

    Attributes:
        name (str): The unique name of the classification.
        name_de (str): The German name of the classification.
        name_en (str): The English name of the classification.
    """
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    
    
    objects = ExaminationIndicationClassificationManager()
    
    def natural_key(self) -> tuple:
        """
        Returns the natural key for the classification.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    def __str__(self) -> str:
        """
        String representation of the classification.

        Returns:
            str: The name of the classification.
        """
        return self.name

    def get_choices(self) -> List['ExaminationIndicationClassificationChoice']:
        """
        Retrieves all choices associated with this classification.

        Returns:
            list: A list of classification choices.
        """
        return list(self.choices.all())

class ExaminationIndicationClassificationChoiceManager(models.Manager):
    """
    Manager for ExaminationIndicationClassificationChoice with custom query methods.
    """
    def get_by_natural_key(self, name: str) -> "ExaminationIndicationClassificationChoice":
        return self.get(name=name)

class ExaminationIndicationClassificationChoice(models.Model):
    """
    Represents a choice within an examination indication classification.

    Attributes:
        name (str): The unique name of the choice.
        name_de (str): The German name of the choice.
        name_en (str): The English name of the choice.
        subcategories (JSONField): Subcategories associated with the choice.
        numerical_descriptors (JSONField): Numerical descriptors for the choice.
        classification (ForeignKey): The classification to which this choice belongs.
    """
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)
    classification = models.ForeignKey(
        ExaminationIndicationClassification, on_delete=models.CASCADE,
        related_name='choices'
    )

    objects = ExaminationIndicationClassificationChoiceManager()

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the classification choice.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    def __str__(self) -> str:
        """
        String representation of the classification choice.

        Returns:
            str: The name of the classification choice.
        """
        return self.name