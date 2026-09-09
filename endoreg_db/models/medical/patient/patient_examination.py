from __future__ import annotations
from typing import TYPE_CHECKING, Optional, Protocol, Any, Unpack, cast

from django.core.exceptions import ValidationError
from django.db import models
from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import (
    dump_patient_examination_report_draft,
    validate_dtypes_p_examination_payload,
)

if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient import Patient  # pyright: ignore
    from endoreg_db.models.medical.examination.examination import Examination
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndication,
    )
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndicationClassificationChoice,
    )
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.report.patient_examination_report import (
        PatientExaminationReport,
    )
    from ...media import (
        AnonymExaminationReport,
        AnonymHistologyReport,
        RawPdfFile,
    )
    from ..finding import Finding
    from .patient_examination_indication import PatientExaminationIndication
    from .patient_finding import PatientFinding


class _PatientExaminationIndicationLike(Protocol):
    examination_indication: "ExaminationIndication | None"
    indication_choice: "ExaminationIndicationClassificationChoice | None"


class PatientExamination(models.Model):
    patient: models.ForeignKey["Patient"] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="patient_examinations"
    )
    examination: models.ForeignKey["Examination | None"] = models.ForeignKey(
        "Examination", on_delete=models.CASCADE, null=True, blank=True
    )
    date_start: models.DateField[Any, Any] = models.DateField(null=True, blank=True)
    date_end: models.DateField[Any, Any] = models.DateField(null=True, blank=True)
    hash: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)
    knowledge_base_module: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    knowledge_base_version: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    dtypes_record: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    dtypes_record_updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    report_draft: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    draft_updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )

    if TYPE_CHECKING:
        patient_id: int | None
        examination_id: int | None
        video_files: models.QuerySet["VideoFile"]
        reports: models.QuerySet["PatientExaminationReport"]
        patient_findings: models.QuerySet["PatientFinding"]
        indications: models.QuerySet["PatientExaminationIndication"]
        raw_pdf_files: models.QuerySet["RawPdfFile"]
        anonymexaminationreport_set: models.QuerySet["AnonymExaminationReport"]
        anonymhistologyreport_set: models.QuerySet["AnonymHistologyReport"]

    @property
    def examination_safe(self):
        if self.examination is None:
            raise ValueError("Examination is not set for this PatientExamination.")
        return self.examination

    # report_files
    class Meta:
        verbose_name = "Patient Examination"
        verbose_name_plural = "Patient Examinations"
        ordering = ["patient", "examination", "date_start"]

    @classmethod
    def get_or_create_pseudo_patient_examination_by_hash(
        cls,
        patient_hash: str,
        examination_hash: str,
        examination_name: Optional[str] = None,
    ) -> tuple["PatientExamination", bool]:
        from ...administration.person import Patient
        from ..examination import Examination

        created = False

        if PatientExamination.objects.filter(
            patient__patient_hash=patient_hash, hash=examination_hash
        ).exists():
            return PatientExamination.objects.get(
                patient__patient_hash=patient_hash, hash=examination_hash
            ), created

        patient, created = Patient.get_or_create_pseudo_patient_by_hash(patient_hash)
        if examination_name is not None:
            examination = Examination.objects.get(name=examination_name)
        else:
            examination = None

        patient_examination = cls.objects.create(
            patient=patient, examination=examination, hash=examination_hash
        )

        patient_examination.save()

        created = True
        return patient_examination, created

    def __str__(self) -> str:
        return f"{self.patient} - {self.examination} - {self.date_start}"

    # override save method to make sure that the hash is always set,
    # if none is existing generate an unique string

    def generate_default_hash(self) -> str:
        # create random hash
        import random
        import string

        _hash = "DEFAULT_HASH_" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=10)
        )

        return _hash

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self.hash:
            self.hash = self.generate_default_hash()
        self.clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        module_name = self.knowledge_base_module.strip()
        version = self.knowledge_base_version.strip()
        if bool(module_name) != bool(version):
            errors["knowledge_base_module"] = (
                "knowledge_base_module and knowledge_base_version must be set together"
            )
        else:
            self.knowledge_base_module = module_name
            self.knowledge_base_version = version
        try:
            self.dtypes_record = validate_dtypes_p_examination_payload(
                self.dtypes_record
            )
        except ValueError as exc:
            errors["dtypes_record"] = str(exc)
        try:
            self.report_draft = dump_patient_examination_report_draft(self.report_draft)
        except ValueError as exc:
            errors["report_draft"] = str(exc)
        if errors:
            raise ValidationError(errors)

    def get_patient_age_at_examination(self) -> int:
        """
        Returns the patient's age at the time of the examination.
        """

        patient = self.patient
        dob = patient.get_dob()
        date_start = self.date_start
        assert dob is not None
        assert date_start is not None
        return (date_start - dob).days // 365

    def get_available_findings(self) -> list["Finding"]:
        """
        Returns all findings that are associated with the examination of this patient examination.
        """

        assert self.examination is not None
        return self.examination.get_available_findings()

    def get_findings(self) -> models.QuerySet["PatientFinding"]:
        """
        Returns all findings that are associated with this patient examination.
        """

        return self.patient_findings.all()

    def get_indications(self) -> models.QuerySet["PatientExaminationIndication"]:
        """
        Returns all indications that are associated with this patient examination.
        """
        return self.indications.all()

    def get_indication_choices(
        self,
    ) -> list["ExaminationIndicationClassificationChoice"]:
        """
        Returns a list of indication choices associated with this patient examination.

        Only includes indication choices that are not None.
        """

        choices: list["ExaminationIndicationClassificationChoice"] = []
        for indication in cast(
            list[_PatientExaminationIndicationLike], list(self.get_indications())
        ):
            indication_choice = indication.indication_choice
            if indication_choice is not None:
                choices.append(indication_choice)
        return choices

    def get_or_create_patient_examination_by_id(
        self, pk: int
    ) -> Optional["PatientExamination"]:
        """Hilfsmethode zum Abrufen oder Erstellen einer PatientExamination nach ID"""
        if not self.objects.filter(pk=pk).exists():
            return None
        else:
            return self.objects.filter(pk=pk).first()

    def create_finding(self, finding: "Finding") -> "PatientFinding":
        """
        Adds a finding to this patient examination.
        """
        from .patient_finding import PatientFinding

        examination = self.examination
        assert examination is not None

        patient_finding = PatientFinding.objects.create(
            patient_examination=self, finding=finding
        )

        return patient_finding
