import datetime as dt
from typing import TYPE_CHECKING, ClassVar, cast

from django.db import models

def get_prediction_information_source() -> "InformationSource":
    """
    Returns the InformationSource instance with the name "prediction".

    Raises:
        AssertionError: If no InformationSource with the name "prediction" exists.
    """
    _source = cast(InformationSourceManager, InformationSource.objects).resolve_by_name(
        "prediction"
    )

    # make sure to return only one object
    assert _source is not None, "No prediction information source found"
    return _source


class InformationSourceManager(models.Manager["InformationSource"]):
    def resolve_by_name(self, name: str) -> "InformationSource | None":
        """Return the deterministic first source for a natural name."""
        normalized_name = str(name).strip()
        return self.filter(name=normalized_name).order_by("pk").first()

    def get_or_create_by_name(
        self, name: str, **defaults: object
    ) -> tuple["InformationSource", bool]:
        """Return an existing source by name before creating a new row."""
        normalized_name = str(name).strip()
        source = self.resolve_by_name(normalized_name)
        if source is not None:
            return source, False
        return self.get_or_create(name=normalized_name, defaults=defaults)

    def get_by_natural_key(self, name: str) -> "InformationSource":
        """
        Retrieves a model instance using its natural key.

        Args:
            name: The natural key value corresponding to the model's 'name' field.

        Returns:
            The model instance that matches the provided natural key.
        """
        source = self.resolve_by_name(name)
        if source is None:
            raise self.model.DoesNotExist(
                f"{self.model._meta.object_name} matching query does not exist."
            )
        return source


class InformationSource(models.Model):
    objects: ClassVar[models.Manager["InformationSource"]] = InformationSourceManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    name: models.CharField[str, str] = models.CharField(max_length=100)

    url: models.URLField[str | None, str | None] = models.URLField(
        blank=True, null=True
    )
    description: models.TextField[str | None, str | None] = models.TextField(
        blank=True, null=True
    )
    date: models.DateField[dt.date | None, dt.date | None] = models.DateField(
        blank=True, null=True
    )
    date_created: models.DateField[dt.date, dt.date] = models.DateField(
        auto_now_add=True
    )
    date_modified: models.DateField[dt.date, dt.date] = models.DateField(
        auto_now=True
    )
    abbreviation: models.CharField[str | None, str | None] = models.CharField(
        max_length=100, blank=True, null=True, unique=True
    )

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Examination,
            ExaminationIndication,
            ExaminationTime,
            Finding,
            FindingClassification,
            FindingIntervention,
            InformationSourceType,
        )

        @property
        def examinations(self) -> "models.Manager[Examination]": ...

        @property
        def examination_indications(
            self,
        ) -> "models.Manager[ExaminationIndication]": ...

        @property
        def examination_times(
            self,
        ) -> "models.Manager[ExaminationTime]": ...

        @property
        def findings(self) -> "models.Manager[Finding]": ...

        @property
        def finding_interventions(
            self,
        ) -> "models.Manager[FindingIntervention]": ...

        @property
        def finding_classifications(
            self,
        ) -> "models.Manager[FindingClassification]": ...

    class Meta:
        verbose_name = "Information Source"
        verbose_name_plural = "Information Sources"

        # add name and abbreviation as index
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["abbreviation"]),
        ]

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key tuple for the information source.

        The tuple contains the object's name, which uniquely identifies it for
        serialization and natural key lookup.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return the name of the InformationSource as its string representation.
        """
        return str(self.name)


class InformationSourceTypeManager(models.Manager["InformationSourceType"]):
    def get_by_natural_key(self, name: str) -> "InformationSourceType":
        """
        Retrieve an instance of the model by its natural key, which is the 'name' field.

        Parameters:
            name (str): The value of the 'name' field to look up.

        Returns:
            The model instance with the specified name.
        """
        return self.get(name=name)


class InformationSourceType(models.Model):
    objects = InformationSourceTypeManager()

    name: models.CharField[str, str] = models.CharField(
        max_length=100, unique=True
    )
    description: models.TextField[str | None, str | None] = (
        models.TextField(blank=True, null=True)
    )

    information_sources: models.ManyToManyField[
        InformationSource,
        InformationSource,
    ] = models.ManyToManyField(
        InformationSource,
        related_name="information_source_types",
        blank=True,
    )

    class Meta:
        verbose_name = "Information Source Type"
        verbose_name_plural = "Information Source Types"

    # information_sources: models.QuerySet["InformationSource"]

    @classmethod
    def get_prediction_type(cls) -> "InformationSourceType":
        """
        Return the InformationSourceType instance with the name "prediction".

        Returns:
            InformationSourceType: The instance representing the "prediction" information source type.

        Raises:
            InformationSourceType.DoesNotExist: If no such instance exists.

        """
        try:
            return cls.objects.get(name="prediction")
        except cls.DoesNotExist as e:
            raise cls.DoesNotExist(
                "The 'prediction' InformationSourceType was not found. Please check your data fixtures or initial data migrations."
            ) from e

    @classmethod
    def get_manual_annotation_type(cls) -> "InformationSourceType":
        """

        Return the InformationSourceType instance representing manual annotation.

        Returns:
            InformationSourceType: The instance with name "annotation".

        Raises:
            AssertionError: If no InformationSourceType with name "annotation" exists.

        """
        try:
            return cls.objects.get(name="manual_annotation")
        except cls.DoesNotExist as e:
            raise cls.DoesNotExist(
                "The 'manual_annotation' InformationSourceType was not found. Please check your data fixtures or initial data migrations."
            ) from e

    def natural_key(self) -> tuple[str]:
        """
        Return a tuple containing the name of the information source type for natural key serialization.
        """
        return (str(self.name),)

    def __str__(self) -> str:
        """
        Return the name of the InformationSourceType as its string representation.
        """
        return str(self.name)
