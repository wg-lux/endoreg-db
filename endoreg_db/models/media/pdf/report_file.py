from typing import TYPE_CHECKING

from django.db import models

from ...utils import DOCUMENT_DIR, STORAGE_DIR

if TYPE_CHECKING:
    from ...administration import (
        Center,
        Patient,
    )
    from ...medical import (
        PatientExamination,
    )
    from ...metadata import SensitiveMeta


class DocumentTypeManager(models.Manager):
    """
    Custom manager for DocumentType.
    """

    def get_by_natural_key(self, name):
        return self.get(name=name)


class DocumentType(models.Model):
    """
    Represents the type of a document.
    """

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = DocumentTypeManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

    class Meta:
        verbose_name = "Document Type"
        verbose_name_plural = "Document Types"


class AbstractDocument(models.Model):
    """
    Abstract base class for documents.
    """

    meta = models.JSONField(blank=True, null=True)
    text = models.TextField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)
    file = models.FileField(
        upload_to=DOCUMENT_DIR.relative_to(STORAGE_DIR).as_posix(),
        blank=True,
        null=True,
    )

    center = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    type = models.ForeignKey(
        DocumentType,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    if TYPE_CHECKING:
        center: models.ForeignKey["Center|None"]
        type: models.ForeignKey["DocumentType|None"]

    class Meta:
        abstract = True


class AbstractExaminationReport(AbstractDocument):
    """
    Abstract base class for examination reports.
    """

    patient = models.ForeignKey("Patient", on_delete=models.DO_NOTHING, blank=True, null=True)

    patient_examination = models.ForeignKey(
        "PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    examiners = models.ManyToManyField(
        "Examiner",
        blank=True,
    )

    sensitive_meta = models.ForeignKey("SensitiveMeta", on_delete=models.SET_NULL, null=True, blank=True)

    if TYPE_CHECKING:
        center: models.ForeignKey["Center|None"]
        type: models.ForeignKey["DocumentType|None"]
        patient: models.ForeignKey["Patient|None"]
        patient_examination: models.ForeignKey["PatientExamination|None"]
        sensitive_meta: models.ForeignKey["SensitiveMeta|None"]

    class Meta:
        abstract = True

    def get_or_create_examiner(self, examiner_first_name, examiner_last_name):
        raise NotImplementedError("Subclasses must implement this method.")

    def set_examination_date_and_time(self, report_meta=None):
        raise NotImplementedError("Subclasses must implement this method.")


class AnonymExaminationReport(AbstractExaminationReport):
    def get_or_create_examiner(self, examiner_first_name: str, examiner_last_name: str):
        from ...administration.person import Examiner

        examiner_center = self.center

        examiner, created = Examiner.objects.get_or_create(
            first_name=examiner_first_name,
            last_name=examiner_last_name,
            center=examiner_center,
        )

        return examiner, created

    def set_examination_date_and_time(self, report_meta=None):
        # TODO
        if not report_meta:
            report_meta = self.meta
        # examination_date_str = report_meta["examination_date"]
        # examination_time_str = report_meta["examination_time"]

        # if examination_date_str:
        #     # TODO: get django DateField compatible date from string (e.g. "2021-01-01")
        #     self.date = date.fromisoformat(examination_date_str)
        # if examination_time_str:
        #     # TODO: get django TimeField compatible time from string (e.g. "12:00")
        #     self.time = time.fromisoformat(examination_time_str)


class AnonymHistologyReport(AbstractExaminationReport):
    """
    Represents a histology report.
    """

    def get_or_create_examiner(self, examiner_first_name, examiner_last_name):
        raise NotImplementedError("Subclasses must implement this method.")
