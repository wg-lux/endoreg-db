from django.db import models
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Examination, Requirement, FindingIntervention
    from endoreg_db.utils.links.requirement_link import RequirementLinks

class ExaminationIndicationManager(models.Manager):
    """
    Manager for ExaminationIndication with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "ExaminationIndication":
        """
        Retrieves an ExaminationIndication instance by its natural key.
        
        Args:
            name: The unique name identifying the examination indication.
        
        Returns:
            The ExaminationIndication instance corresponding to the specified name.
        """
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
        expected_interventions (ManyToManyField): Expected interventions for this indication.
    """

    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    classifications = models.ManyToManyField(
        "ExaminationIndicationClassification",
        related_name="indications",
        blank=True,
    )

    examination = models.ForeignKey(
        "Examination",
        on_delete=models.CASCADE,
        related_name="indications",
    )

    expected_interventions = models.ManyToManyField(
        "FindingIntervention",
        related_name="indications",
        blank=True,
    )

    objects = ExaminationIndicationManager()

    if TYPE_CHECKING:
        classifications: "models.ManyToManyField[ExaminationIndicationClassification]"
        examination: "Examination"
        related_requirements: "models.QuerySet[Requirement]"
        expected_interventions: "models.ManyToManyField[FindingIntervention]"

    @property
    def links(self) -> "RequirementLinks":
        """
        Returns a RequirementLinks instance containing the links related to this indication.
        
        This property aggregates the related requirements and classifications into a RequirementLinks object.
        
        Returns:
            RequirementLinks: An instance containing the links related to this indication.
        """
        from endoreg_db.utils.links.requirement_link import RequirementLinks
        return RequirementLinks(
            examination_indications=[self],
            examinations=[self.examination],
            finding_interventions=list(self.expected_interventions.all()),
        )

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
        return str(self.name)

    def get_choices(self) -> List["ExaminationIndicationClassificationChoice"]:
        """
        Retrieves all classification choices for the indication.
        
        Aggregates and returns the choices from each classification associated with the indication.
        
        Returns:
            List[ExaminationIndicationClassificationChoice]: A list of classification choices.
        """
        classifications = self.classifications.all()
        choices = []
        for classification in classifications:
            choices.extend(classification.choices.all())
        return choices

    def get_examination(self) -> "Examination":
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
        """
        Retrieves an ExaminationIndicationClassification by its natural key.
        
        Args:
            name: The unique name identifying the classification.
        
        Returns:
            The ExaminationIndicationClassification instance corresponding to the given name.
        """
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
    description = models.TextField(blank=True, null=True)
    examination = models.ForeignKey(
        "Examination",
        on_delete=models.CASCADE,
        related_name="indication_classifications",
        blank=True,
        null=True,
    )

    if TYPE_CHECKING:
        examination: "Examination"
        choices: "models.QuerySet[ExaminationIndicationClassificationChoice]"

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
        return str(self.name)

    def get_choices(self) -> List["ExaminationIndicationClassificationChoice"]:
        """
        Retrieves all classification choices associated with this classification.
        
        Returns:
            List[ExaminationIndicationClassificationChoice]: A list of classification choice instances.
        """
        return list(self.choices.all())


class ExaminationIndicationClassificationChoiceManager(models.Manager):
    """
    Manager for ExaminationIndicationClassificationChoice with custom query methods.
    """

    def get_by_natural_key(
        self, name: str
    ) -> "ExaminationIndicationClassificationChoice":
        """
        Retrieves an ExaminationIndicationClassificationChoice instance by its natural key.
        
        Args:
            name: The unique name serving as the natural key for the classification choice.
        
        Returns:
            An ExaminationIndicationClassificationChoice instance corresponding to the given name.
        """
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
        ExaminationIndicationClassification,
        on_delete=models.CASCADE,
        related_name="choices",
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
        return str(self.name)
