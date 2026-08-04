from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        FindingType,
        InformationSource,
    )


class FindingClassificationTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return cast("FindingClassificationType", self.get(name=name))


class FindingClassificationType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    objects: models.Manager["FindingClassificationType"] = (
        FindingClassificationTypeManager()
    )

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)


class FindingClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return cast("FindingClassification", self.get(name=name))


class FindingClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    finding_types: "models.ManyToManyField[FindingType, FindingType]" = (
        models.ManyToManyField(
            "FindingType", blank=True, related_name="finding_classifications"
        )
    )
    choices: "models.ManyToManyField['FindingClassificationChoice', 'FindingClassificationChoice']" = models.ManyToManyField(
        "FindingClassificationChoice", related_name="classifications", blank=True
    )

    classification_types = models.ManyToManyField(
        to=FindingClassificationType,
        # on_delete=models.CASCADE
    )
    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="finding_classifications",
        blank=True,
    )

    @property
    def examinations(self):
        from endoreg_db.models import Examination

        return Examination.objects.filter(findings__finding_classifications=self)

    objects: models.Manager["FindingClassification"] = FindingClassificationManager()

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

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

    def get_choices(self):
        """
        Return all choices associated with this classification.

        Returns:
                QuerySet: All related FindingClassificationChoice instances.
        """
        return self.choices.all()


class FindingClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve an instance by its unique name using the natural key.

        Parameters:
            name (str): The unique name identifying the instance.

        Returns:
            The model instance with the specified name.
        """
        return self.get(name=name)


class FindingClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    subcategories = models.JSONField(default=dict)
    numerical_descriptors = models.JSONField(default=dict)
    objects: models.Manager["FindingClassificationChoice"] = (
        FindingClassificationChoiceManager()
    )

    if TYPE_CHECKING:
        from endoreg_db.models import PatientFindingClassification

        classifications: models.QuerySet["FindingClassification"]
        patient_finding_classifications: models.QuerySet["PatientFindingClassification"]

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        classifications_names = ", ".join([c.name for c in self.classifications.all()])
        _str = f"{self.name} ({classifications_names})"
        return _str
