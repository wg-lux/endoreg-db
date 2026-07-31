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
        FindingType,
        InformationSource,
    )


class FindingClassificationTypeManager(models.Manager["FindingClassificationType"]):
    def get_by_natural_key(self, name: str) -> "FindingClassificationType":
        return self.get(name=name)


class FindingClassificationType(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True)
    objects = FindingClassificationTypeManager()

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)


class FindingClassificationManager(models.Manager["FindingClassification"]):
    def get_by_natural_key(self, name: str) -> "FindingClassification":
        return self.get(name=name)


class FindingClassification(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True)
    finding_types: "models.ManyToManyField[FindingType, FindingType]" = (
        models.ManyToManyField(
            "FindingType", blank=True, related_name="finding_classifications"
        )
    )
    choices: "models.ManyToManyField['FindingClassificationChoice', 'FindingClassificationChoice']" = models.ManyToManyField(
        "FindingClassificationChoice", related_name="classifications", blank=True
    )

    classification_types: "models.ManyToManyField[FindingClassificationType, FindingClassificationType]" = models.ManyToManyField(
        to=FindingClassificationType,
        # on_delete=models.CASCADE
    )
    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="finding_classifications",
        blank=True,
    )

    @property
    def examinations(self) -> models.QuerySet["Examination"]:
        from endoreg_db.models import Examination

        return Examination.objects.filter(findings__finding_classifications=self)

    objects = FindingClassificationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Examination,
            Finding,
            FindingType,
            InformationSource,
            PatientFindingClassification,
        )

        @property
        def findings(self) -> "models.Manager[Finding]": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)

    def get_choices(self) -> models.QuerySet["FindingClassificationChoice"]:
        """
        Return all choices associated with this classification.

        Returns:
                QuerySet: All related FindingClassificationChoice instances.
        """
        return self.choices.all()


class FindingClassificationChoiceManager(models.Manager["FindingClassificationChoice"]):
    def get_by_natural_key(self, name: str) -> "FindingClassificationChoice":
        """
        Retrieve an instance by its unique name using the natural key.

        Parameters:
            name (str): The unique name identifying the instance.

        Returns:
            The model instance with the specified name.
        """
        return self.get(name=name)


class FindingClassificationChoice(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    description: models.TextField[Any, Any] = models.TextField(blank=True)
    subcategories: models.JSONField[Any, Any] = models.JSONField(default=dict)
    numerical_descriptors: models.JSONField[Any, Any] = models.JSONField(default=dict)
    objects = FindingClassificationChoiceManager()

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
        from endoreg_db.models import PatientFindingClassification

        classifications: models.QuerySet["FindingClassification"]
        patient_finding_classifications: models.QuerySet["PatientFindingClassification"]

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        classifications_names = ", ".join(c.name for c in self.classifications.all())
        _str = f"{self.name} ({classifications_names})"
        return _str
