from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.schemas.classification_choice import (
    ClassificationChoiceJSONValidationError,
    validate_classification_choice_json_fields,
)

if TYPE_CHECKING:
    from endoreg_db.models import (
        Examination,
        ExaminationIndicationClassification,
        ExaminationIndicationClassificationChoice,
        FindingIntervention,
        InformationSource,
    )
    from endoreg_db.utils.links import ModelLinks


class ExaminationIndicationManager(models.Manager["ExaminationIndication"]):
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

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    classifications: "models.ManyToManyField[ExaminationIndicationClassification, ExaminationIndicationClassification]" = models.ManyToManyField(
        "ExaminationIndicationClassification",
        related_name="indications",
        blank=True,
    )

    expected_interventions: "models.ManyToManyField[FindingIntervention, FindingIntervention]" = models.ManyToManyField(
        "FindingIntervention",
        related_name="indications",
        blank=True,
    )

    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="examination_indications",
        blank=True,
    )

    objects = ExaminationIndicationManager()

    if TYPE_CHECKING:

        @property
        def examinations(self) -> "models.Manager[Examination]": ...

    @property
    def links(self) -> "ModelLinks":
        """
        Aggregates related classifications, examinations, and interventions into a ModelLinks object.

        Returns:
            A ModelLinks instance representing all entities linked to this examination indication.
        """
        from endoreg_db.utils.links import ModelLinks

        return ModelLinks(
            examination_indications=[self],
            examinations=list(self.examinations.all()),
            finding_interventions=list(self.expected_interventions.all()),
        )

    def natural_key(self) -> tuple[str]:
        """
        Returns a tuple containing the unique name of the indication as its natural key.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        String representation of the indication.

        Returns:
            str: The name of the indication.
        """
        return str(self.name)


class ExaminationIndicationClassificationManager(
    models.Manager["ExaminationIndicationClassification"]
):
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

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    choices: "models.ManyToManyField[ExaminationIndicationClassificationChoice, ExaminationIndicationClassificationChoice]" = models.ManyToManyField(
        "ExaminationIndicationClassificationChoice",
        related_name="classifications",
        blank=True,
    )

    objects = ExaminationIndicationClassificationManager()

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the classification.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        String representation of the classification.

        Returns:
            str: The name of the classification.
        """
        return str(self.name)


class ExaminationIndicationClassificationChoiceManager(
    models.Manager["ExaminationIndicationClassificationChoice"]
):
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

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    subcategories: models.JSONField[Any, Any] = models.JSONField(default=dict)
    numerical_descriptors: models.JSONField[Any, Any] = models.JSONField(default=dict)

    objects = ExaminationIndicationClassificationChoiceManager()

    def clean(self) -> None:
        super().clean()
        try:
            validate_classification_choice_json_fields(self)
        except ClassificationChoiceJSONValidationError as exc:
            raise ValidationError({exc.field_name: str(exc)}) from exc

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    if TYPE_CHECKING:
        from lx_dtypes.models.contracts.examination_indication import (
            ExaminationIndicationClassificationChoiceCore,
        )

        @property
        def contract(self) -> ExaminationIndicationClassificationChoiceCore: ...

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the classification choice.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        String representation of the classification choice.

        Returns:
            str: The name of the classification choice.
        """
        return str(self.name)
