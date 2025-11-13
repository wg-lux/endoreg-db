from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import InformationSourceType


def get_prediction_information_source():
    """
    Returns the InformationSource instance with the name "prediction".

    Raises:
        AssertionError: If no InformationSource with the name "prediction" exists.
    """
    _source = InformationSource.objects.get(name="prediction")

    # make sure to return only one object
    assert _source, "No prediction information source found"
    return _source


class InformationSourceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieves a model instance using its natural key.

        Args:
            name: The natural key value corresponding to the model's 'name' field.

        Returns:
            The model instance that matches the provided natural key.
        """
        return self.get(name=name)


class InformationSource(models.Model):
    objects = InformationSourceManager()

    name = models.CharField(max_length=100)

    url = models.URLField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    date_created = models.DateField(auto_now_add=True)
    date_modified = models.DateField(auto_now=True)
    abbreviation = models.CharField(max_length=100, blank=True, null=True, unique=True)

    if TYPE_CHECKING:
        # information_source_types: models.QuerySet["InformationSourceType"]
        # Avoid self-referential import; use forward references instead
        pass

    class Meta:
        verbose_name = "Information Source"
        verbose_name_plural = "Information Sources"

        # add name and abbreviation as index
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["abbreviation"]),
        ]

    def natural_key(self):
        """
        Returns the natural key tuple for the information source.

        The tuple contains the object's name, which uniquely identifies it for
        serialization and natural key lookup.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the name of the InformationSource as its string representation.
        """
        return str(self.name)


class InformationSourceTypeManager(models.Manager):
    def get_by_natural_key(self, name):
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

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    information_sources = models.ManyToManyField(
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
            raise cls.DoesNotExist("The 'prediction' InformationSourceType was not found. Please check your data fixtures or initial data migrations.") from e

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

    def natural_key(self):
        """
        Return a tuple containing the name of the information source type for natural key serialization.
        """
        return (self.name,)

    def __str__(self):
        """
        Return the name of the InformationSourceType as its string representation.
        """
        return str(self.name)
