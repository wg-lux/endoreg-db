from django.db import models
from typing import List


class ExaminationManager(models.Manager):
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
        name_de (str): The German name of the examination.
        name_en (str): The English name of the examination.
        examination_types (ManyToManyField): The types associated with the examination.
        date (DateField): The date of the examination.
        time (TimeField): The time of the examination.
    """

    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    examination_types = models.ManyToManyField("ExaminationType", blank=True)

    objects = ExaminationManager()

    def __str__(self) -> str:
        """
        String representation of the examination.

        Returns:
            str: The name of the examination.
        """
        return self.name

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the examination.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)

    def get_available_findings(self) -> List["Finding"]:
        """
        Retrieves all findings associated with the examination.

        Returns:
            list: A list of findings related to the examination.
        """
        from endoreg_db.models import Finding

        findings: List[Finding] = [_ for _ in self.findings.all()]
        return findings

    class Meta:
        verbose_name = "Examination"
        verbose_name_plural = "Examinations"
        ordering = ["name"]
