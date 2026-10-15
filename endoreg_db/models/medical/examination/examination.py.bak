from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import (
        ExaminationIndication,
        ExaminationTime,
        ExaminationType,
        Finding,
        InformationSource,
    )
    from endoreg_db.utils.links import ModelLinks


class ExaminationManager(models.Manager["Examination"]):
    """
    Manager for Examination with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "Examination":
        return self.get(name=name)


class Examination(models.Model):
    """
    Represents an examination with associated details.

    Attributes:
        name (str): The unique name of the examination.
        examination_types (ManyToManyField): The types associated with the examination.
    """

    name: models.CharField[str, str] = models.CharField(max_length=100, unique=True)
    examination_types: "models.ManyToManyField[ExaminationType, ExaminationType]" = (
        models.ManyToManyField("ExaminationType", blank=True)
    )
    description: models.TextField[str, str] = models.TextField(blank=True, null=True)
    indications: "models.ManyToManyField[ExaminationIndication, ExaminationIndication]" = models.ManyToManyField(
        "ExaminationIndication",
        related_name="examinations",
        blank=True,
    )
    examination_times: "models.ManyToManyField[ExaminationTime, ExaminationTime]" = (
        models.ManyToManyField(
            "ExaminationTime",
            related_name="examinations",
            blank=True,
        )
    )

    findings: "models.ManyToManyField[Finding, Finding]" = models.ManyToManyField(
        "Finding",
        blank=True,
        related_name="examinations",
    )
    information_sources: "models.ManyToManyField[InformationSource, InformationSource]" = models.ManyToManyField(
        "InformationSource",
        related_name="examinations",
        blank=True,
    )

    objects = ExaminationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import FindingClassification

        @property
        def finding_classifications(
            self,
        ) -> "models.Manager[FindingClassification]": ...

    @property
    def links(self) -> "ModelLinks":
        """
        Returns a ModelLinks instance containing all models related to this examination.
        This should include:
        - Examination, Finding, FindingClassification, ExaminationIndication
        """

        from endoreg_db.utils.links import ModelLinks

        return ModelLinks(
            examinations=[self],
            findings=list(self.findings.all()),
            finding_classifications=list(self.finding_classifications.all()),
            examination_indications=list(self.indications.all()),
        )

    def __str__(self) -> str:
        """
        String representation of the examination.

        Returns:
            str: The name of the examination.
        """
        return str(self.name)

    def natural_key(self) -> tuple[str]:
        """
        Returns the natural key for the examination.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (str(self.name),)

    def get_available_findings(self) -> list["Finding"]:
        """
        Retrieves all findings associated with the examination.

        Returns:
            list: A list of findings related to the examination.
        """
        findings: list[Finding] = [finding for finding in self.findings.all()]
        return findings

    class Meta:
        verbose_name = "Examination"
        verbose_name_plural = "Examinations"
        ordering = ["name"]
