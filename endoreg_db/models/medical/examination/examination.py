from typing import TYPE_CHECKING, List

from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import Finding
    from endoreg_db.utils.links.requirement_link import RequirementLinks


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
        examination_types (ManyToManyField): The types associated with the examination.
    """

    name = models.CharField(max_length=100, unique=True)
    examination_types = models.ManyToManyField("ExaminationType", blank=True)
    description = models.TextField(blank=True, null=True)

    objects = ExaminationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import ExaminationIndication, Finding, FindingClassification

        @property
        def finding_classifications(self) -> "models.manager.RelatedManager[FindingClassification]": ...

        @property
        def indications(self) -> "models.manager.RelatedManager[ExaminationIndication]": ...

        @property
        def findings(self) -> "models.manager.RelatedManager[Finding]": ...

        @property
        def exam_reqset_links(self) -> "models.manager.RelatedManager[ExaminationRequirementSet]": ...

    @property
    def links(self) -> "RequirementLinks":
        """
        Returns a RequirementLinks instance containing all models related to this examination.
        This should include:
        - Examination, Finding, FindingClassification, ExaminationIndication
        """

        from endoreg_db.utils.links.requirement_link import RequirementLinks

        return RequirementLinks(
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


class ExaminationRequirementSetManager(models.Manager):
    """
    Manager for ExaminationRequirementSet with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "ExaminationRequirementSet":
        return self.get(name=name)


class ExaminationRequirementSet(models.Model):
    """
    Through table for Examination ↔ RequirementSet link.
    Lets you store per-link metadata (order, default, etc.)
    """

    examinations = models.ManyToManyField(
        "Examination",
        related_name="exam_reqset_links",
        blank=True,
    )
    # requirement_set = models.ForeignKey(
    #     "RequirementSet",
    #     on_delete=models.CASCADE,
    #     related_name="reqset_exam_links",
    # )
    # Optional metadata
    enabled_by_default = models.BooleanField(default=False)

    name = models.CharField(max_length=100, unique=True)

    objects = ExaminationRequirementSetManager()

    # class Meta:
    #     unique_together = ("examination", "requirement_set")

    # def __str__(self) -> str:
    #     return f"{self.examination} ↔ {self.requirement_set} (prio={self.priority})"

    def natural_key(self) -> tuple:
        """
        Returns the natural key for the ExaminationRequirementSet.

        Returns:
            tuple: The natural key consisting of the name.
        """
        return (self.name,)
