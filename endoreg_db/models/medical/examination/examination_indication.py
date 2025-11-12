from typing import TYPE_CHECKING, List, Optional, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import Examination, FindingIntervention, Requirement
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
        classifications (ManyToManyField): The classifications associated with the indication.
        examinations (ManyToManyField): The examinations associated with the indication.
        expected_interventions (ManyToManyField): Expected interventions for this indication.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    classifications = models.ManyToManyField(
        "ExaminationIndicationClassification",
        related_name="indications",
        blank=True,
    )

    examinations = models.ManyToManyField(
        "Examination",
        related_name="indications",
        blank=True,
    )

    expected_interventions = models.ManyToManyField(
        "FindingIntervention",
        related_name="indications",
        blank=True,
    )

    objects = ExaminationIndicationManager()

    if TYPE_CHECKING:
        classifications = cast(models.manager.RelatedManager["ExaminationIndicationClassification"], classifications)
        examinations = cast(models.manager.RelatedManager["Examination"], examinations)
        expected_interventions = cast(models.manager.RelatedManager["FindingIntervention"], expected_interventions)

        @property
        def related_requirements(self) -> "models.manager.RelatedManager[Requirement]": ...

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates related requirements, classifications, examination, and interventions into a RequirementLinks object.

        Returns:
            A RequirementLinks instance representing all entities linked to this examination indication.
        """
        from endoreg_db.utils.links.requirement_link import RequirementLinks

        return RequirementLinks(
            examination_indications=[self],
            examinations=list(self.examinations.all()),
            finding_interventions=list(self.expected_interventions.all()),
        )

    def natural_key(self) -> tuple:
        """
        Returns a tuple containing the unique name of the indication as its natural key.
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

    def get_examination(self) -> Optional["Examination"]:
        """
        Returns the first examination associated with this indication, or None if no examinations exist.

        Note: Since this is now a many-to-many relationship, this method returns the first examination.
        Consider using get_examinations() for accessing all related examinations.
        """
        return self.examinations.first()

    def get_examinations(self) -> List["Examination"]:
        """
        Returns all examinations associated with this indication.

        Returns:
            List[Examination]: A list of all examinations linked to this indication.
        """
        return list(self.examinations.all())


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
        description (str): Optional description of the classification.
        examinations (ManyToManyField): The examinations associated with this classification.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    examinations = models.ManyToManyField(
        "Examination",
        related_name="indication_classifications",
        blank=True,
    )

    objects = ExaminationIndicationClassificationManager()

    if TYPE_CHECKING:
        examinations: "models.ManyToManyField[Examination, Examination]"
        choices: "models.QuerySet[ExaminationIndicationClassificationChoice]"

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

    def get_examination(self) -> Optional["Examination"]:
        """
        Returns the first examination associated with this classification, or None if no examinations exist.

        Note: Since this is now a many-to-many relationship, this method returns the first examination.
        Consider using get_examinations() for accessing all related examinations.
        """
        return self.examinations.first()

    def get_examinations(self) -> List["Examination"]:
        """
        Returns all examinations associated with this classification.

        Returns:
            List[Examination]: A list of all examinations linked to this classification.
        """
        return list(self.examinations.all())


class ExaminationIndicationClassificationChoiceManager(models.Manager):
    """
    Manager for ExaminationIndicationClassificationChoice with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "ExaminationIndicationClassificationChoice":
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
        subcategories (JSONField): Subcategories associated with the choice.
        numerical_descriptors (JSONField): Numerical descriptors for the choice.
        classification (ForeignKey): The classification to which this choice belongs.
    """

    name = models.CharField(max_length=255, unique=True)
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
