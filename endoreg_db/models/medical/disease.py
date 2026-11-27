from __future__ import annotations
from typing import TYPE_CHECKING, Any, Unpack

from django.core.exceptions import ValidationError
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.classification_choice import (
    ClassificationChoiceJSONValidationError,
    validate_classification_choice_json_fields,
)

if TYPE_CHECKING:
    from .patient import PatientDisease


class Disease(models.Model):
    """
    Represents a specific disease or medical condition.

    Can define associated subcategories and numerical descriptors applicable to the disease itself.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    subcategories: models.JSONField[Any, Any] = models.JSONField(default=dict)
    numerical_descriptors: models.JSONField[Any, Any] = models.JSONField(default=dict)

    objects = NameNaturalKeyManager["Disease"]()

    def clean(self) -> None:
        super().clean()
        try:
            validate_classification_choice_json_fields(self)
        except ClassificationChoiceJSONValidationError as exc:
            raise ValidationError({exc.field_name: str(exc)}) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)

    if TYPE_CHECKING:

        @property
        def disease_classifications(
            self,
        ) -> models.QuerySet["DiseaseClassification"]: ...

        @property
        def patient_diseases(
            self,
        ) -> models.QuerySet["PatientDisease"]: ...

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the disease."""
        return str(self.name)

    def get_classifications(self) -> list["DiseaseClassification"]:
        """
        Retrieves all classification systems associated with this disease.

        Returns:
            list[DiseaseClassification]: A list of related disease classification objects.
        """
        classifications = [item for item in self.disease_classifications.all()]
        return classifications


class DiseaseClassification(models.Model):
    """
    Represents a classification system applicable to a specific disease (e.g., Forrest classification for ulcers).
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    disease: models.ForeignKey[Any] = models.ForeignKey(
        Disease, on_delete=models.CASCADE, related_name="disease_classifications"
    )

    objects = NameNaturalKeyManager["DiseaseClassification"]()

    if TYPE_CHECKING:

        @property
        def disease_classification_choices(
            self,
        ) -> models.Manager["DiseaseClassificationChoice"]: ...

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the classification."""
        return str(self.name)

    def get_choices(self) -> list["DiseaseClassificationChoice"]:
        """
        Retrieves all choices within this classification system.

        Returns:
            list[DiseaseClassificationChoice]: A list of related disease classification choices.
        """
        choices = [item for item in self.disease_classification_choices.all()]
        return choices


class DiseaseClassificationChoice(models.Model):
    """
    Represents a specific choice within a disease classification system (e.g., Forrest IIa).
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

    disease_classification: models.ForeignKey["DiseaseClassification | None"] = (
        models.ForeignKey(
            DiseaseClassification,
            on_delete=models.CASCADE,
            related_name="disease_classification_choices",
        )
    )

    objects = NameNaturalKeyManager["DiseaseClassificationChoice"]()

    if TYPE_CHECKING:
        pass

        @property
        def patient_diseases(
            self,
        ) -> models.Manager["PatientDisease"]: ...

    def natural_key(self) -> tuple[str]:
        """Returns the natural key (name) as a tuple."""
        return (str(self.name),)

    def __str__(self) -> str:
        """Returns the name of the classification choice."""
        return str(self.name)
