from __future__ import annotations

from datetime import date, time
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from ...utils import DOCUMENT_DIR, STORAGE_DIR

if TYPE_CHECKING:
    from ...administration import Center, Examiner, Patient
    from ...medical.patient.patient_examination import PatientExamination
    from ...metadata.sensitive_meta import SensitiveMeta

NoDocumentRelationValue: TypeAlias = NoneType
NoDocumentTextValue: TypeAlias = NoneType
NoDocumentDateValue: TypeAlias = NoneType
NoDocumentTimeValue: TypeAlias = NoneType
NoDocumentMetaValue: TypeAlias = NoneType
DocumentDescription: TypeAlias = "str | NoDocumentTextValue"
DocumentMeta: TypeAlias = "JsonObject | NoDocumentMetaValue"
DocumentDate: TypeAlias = "date | NoDocumentDateValue"
DocumentTime: TypeAlias = "time | NoDocumentTimeValue"
DocumentCenter: TypeAlias = "Center | NoDocumentRelationValue"
DocumentTypeRelation: TypeAlias = "DocumentType | NoDocumentRelationValue"
DocumentPatient: TypeAlias = "Patient | NoDocumentRelationValue"
DocumentPatientExamination: TypeAlias = "PatientExamination | NoDocumentRelationValue"
DocumentSensitiveMeta: TypeAlias = "SensitiveMeta | NoDocumentRelationValue"


class DocumentTypeManager(models.Manager["DocumentType"]):
    """
    Custom manager for DocumentType.
    """

    def get_by_natural_key(self, name: str) -> "DocumentType":
        return self.get(name=name)


class DocumentType(models.Model):
    """
    Represents the type of a document.
    """

    name: models.CharField[str, str] = models.CharField(max_length=255, unique=True)
    description: models.TextField[DocumentDescription, DocumentDescription] = (
        models.TextField(blank=True, null=True)
    )

    objects = DocumentTypeManager()

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)

    class Meta:
        verbose_name = "Document Type"
        verbose_name_plural = "Document Types"


class AbstractDocument(models.Model):
    """
    Abstract base class for documents.
    """

    meta: models.JSONField[DocumentMeta, DocumentMeta] = models.JSONField(
        blank=True, null=True
    )
    text: models.TextField[DocumentDescription, DocumentDescription] = models.TextField(
        blank=True, null=True
    )
    date: models.DateField[DocumentDate, DocumentDate] = models.DateField(
        blank=True, null=True
    )
    time: models.TimeField[DocumentTime, DocumentTime] = models.TimeField(
        blank=True, null=True
    )
    file: models.FileField = models.FileField(
        upload_to=DOCUMENT_DIR.relative_to(STORAGE_DIR).as_posix(),
        blank=True,
        null=True,
    )

    center: models.ForeignKey[DocumentCenter, DocumentCenter] = models.ForeignKey(
        "endoreg_db.Center",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    type: models.ForeignKey[DocumentTypeRelation, DocumentTypeRelation] = (
        models.ForeignKey(
            DocumentType,
            on_delete=models.SET_NULL,
            blank=True,
            null=True,
        )
    )

    if TYPE_CHECKING:
        pass

    class Meta:
        abstract = True


class AbstractExaminationReport(AbstractDocument):
    """
    Abstract base class for examination reports.
    """

    patient: models.ForeignKey[DocumentPatient, DocumentPatient] = models.ForeignKey(
        "endoreg_db.Patient", on_delete=models.DO_NOTHING, blank=True, null=True
    )

    patient_examination: models.ForeignKey[
        DocumentPatientExamination, DocumentPatientExamination
    ] = models.ForeignKey(
        "endoreg_db.PatientExamination",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    examiners: models.ManyToManyField["Examiner", "Examiner"] = models.ManyToManyField(
        "endoreg_db.Examiner",
        blank=True,
    )

    sensitive_meta: models.ForeignKey[DocumentSensitiveMeta, DocumentSensitiveMeta] = (
        models.ForeignKey(
            "endoreg_db.SensitiveMeta", on_delete=models.SET_NULL, null=True, blank=True
        )
    )

    if TYPE_CHECKING:
        pass

    class Meta(AbstractDocument.Meta):
        abstract = True

    def get_or_create_examiner(
        self, examiner_first_name: str, examiner_last_name: str
    ) -> tuple["Examiner", bool]:
        raise NotImplementedError("Subclasses must implement this method.")

    def set_examination_date_and_time(self, report_meta: DocumentMeta = None) -> None:
        raise NotImplementedError("Subclasses must implement this method.")


class AnonymExaminationReport(AbstractExaminationReport):
    if TYPE_CHECKING:
        patient_examination_id: int | None

    def get_or_create_examiner(
        self, examiner_first_name: str, examiner_last_name: str
    ) -> tuple["Examiner", bool]:
        from ...administration.person import Examiner

        examiner_center = self.center

        examiner, created = Examiner.objects.get_or_create(
            first_name=examiner_first_name,
            last_name=examiner_last_name,
            center=examiner_center,
        )

        return examiner, created

    def set_examination_date_and_time(self, report_meta: DocumentMeta = None) -> None:
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

    def get_or_create_examiner(
        self, examiner_first_name: str, examiner_last_name: str
    ) -> tuple["Examiner", bool]:
        raise NotImplementedError("Subclasses must implement this method.")
