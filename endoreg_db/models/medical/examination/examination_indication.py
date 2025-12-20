from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        Examination,
        FindingIntervention,
        InformationSource,
        Requirement,
    )
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

    expected_interventions = models.ManyToManyField(
        "FindingIntervention",
        related_name="indications",
        blank=True,
    )

    information_sources = models.ManyToManyField(
        "InformationSource",
        related_name="examination_indications",
        blank=True,
    )

    objects = ExaminationIndicationManager()

    if TYPE_CHECKING:
        classifications = cast(
            models.manager.RelatedManager["ExaminationIndicationClassification"],
            classifications,
        )
        expected_interventions = cast(
            models.manager.RelatedManager["FindingIntervention"], expected_interventions
        )
        information_sources = cast(
            models.manager.RelatedManager["InformationSource"], information_sources
        )

        @property
        def related_requirements(
            self,
        ) -> "models.manager.RelatedManager[Requirement]": ...

        @property
        def examinations(self) -> "models.manager.RelatedManager[Examination]": ...

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
    choices = models.ManyToManyField(
        "ExaminationIndicationClassificationChoice",
        related_name="classifications",
        blank=True,
    )

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
        subcategories (JSONField): Subcategories associated with the choice.
        numerical_descriptors (JSONField): Numerical descriptors for the choice.
        classification (ForeignKey): The classification to which this choice belongs.
    """

    name = models.CharField(max_length=255, unique=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)

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
