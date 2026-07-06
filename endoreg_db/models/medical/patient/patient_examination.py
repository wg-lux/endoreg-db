from __future__ import annotations
from collections.abc import Iterable
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional, Protocol, cast

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from endoreg_db.schemas import validate_dtypes_p_examination_payload

if TYPE_CHECKING:
    from endoreg_db.models.administration.person.patient.patient import Patient  # pyright: ignore
    from endoreg_db.models.medical.examination.examination import Examination
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndication,
    )
    from endoreg_db.models.medical.examination.examination_indication import (
        ExaminationIndicationClassificationChoice,
    )
    from endoreg_db.models.medical.finding.finding_classification import (
        FindingClassification,
        FindingClassificationChoice,
    )
    from endoreg_db.models.medical.finding.finding_intervention import (
        FindingIntervention,
    )
    from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.utils.links import ModelLinks

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


class _PatientFindingClassificationLike(Protocol):
    classification: "FindingClassification | None"
    classification_choice: "FindingClassificationChoice | None"


class _PatientFindingInterventionLike(Protocol):
    intervention: "FindingIntervention | None"


class PatientExamination(models.Model):
    patient: models.ForeignKey["Patient | None"] = models.ForeignKey(
        "Patient", on_delete=models.CASCADE, related_name="patient_examinations"
    )
    examination: models.ForeignKey["Examination | None"] = models.ForeignKey(
        "Examination", on_delete=models.CASCADE, null=True, blank=True
    )
    video: models.OneToOneField["VideoFile | None"] = models.OneToOneField(
        "VideoFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="patient_examination",
    )

    date_start: models.DateField[date | None] = models.DateField(null=True, blank=True)
    date_end: models.DateField[date | None] = models.DateField(null=True, blank=True)
    hash: models.CharField[str] = models.CharField(max_length=255, unique=True)
    knowledge_base_module: models.CharField[str] = models.CharField(
        max_length=255, blank=True, default=""
    )
    knowledge_base_version: models.CharField[str] = models.CharField(
        max_length=255, blank=True, default=""
    )
    dtypes_record: models.JSONField[JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    dtypes_record_updated_at: models.DateTimeField[datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    report_draft: models.JSONField[JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    draft_updated_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )

    if TYPE_CHECKING:
        patient_id: int | None
        examination_id: int | None
        video_id: int | None
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

    def save(
        self,
        force_insert: bool = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self.hash:
            self.hash = self.generate_default_hash()
        self.assign_knowledge_base_identity()
        self.clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def clean(self) -> None:
        super().clean()
        try:
            self.dtypes_record = validate_dtypes_p_examination_payload(
                self.dtypes_record
            )
        except ValueError as exc:
            raise ValidationError({"dtypes_record": str(exc)}) from exc

    def assign_knowledge_base_identity(self) -> None:
        if self.knowledge_base_module and self.knowledge_base_version:
            return

        from endoreg_db.services.knowledge_base_identity import (
            get_configured_knowledge_base_identity,
        )

        knowledge_base_identity = get_configured_knowledge_base_identity()
        if knowledge_base_identity is None:
            return

        knowledge_base_module, knowledge_base_version = knowledge_base_identity
        if not self.knowledge_base_module:
            self.knowledge_base_module = knowledge_base_module
        if not self.knowledge_base_version:
            self.knowledge_base_version = knowledge_base_version

    def get_patient_age_at_examination(self) -> int:
        """
        Returns the patient's age at the time of the examination.
        """

        patient = cast(Patient, self.patient)
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

    @property
    def links(self) -> "ModelLinks":
        """
        Aggregates and returns all related model instances for linked-model traversal
        as a ModelLinks object.

        This includes:
        - All findings associated with this examination
        - All classifications and choices from those findings
        - All interventions from those findings
        - Examination indications and their choices
        - Patient lab values
        """
        from endoreg_db.utils.links import ModelLinks

        # Get all PatientExaminationIndication instances linked to this PatientExamination
        patient_exam_indications = cast(
            list[_PatientExaminationIndicationLike], list(self.indications.all())
        )

        examination_indications_list: list[ExaminationIndication] = []
        indication_choices_list: list[ExaminationIndicationClassificationChoice] = []

        for pei in patient_exam_indications:
            examination_indication = pei.examination_indication
            if examination_indication is not None:
                examination_indications_list.append(examination_indication)
            indication_choice = pei.indication_choice
            if indication_choice is not None:
                indication_choices_list.append(indication_choice)

        # Fetch all patient lab values associated with this patient examination's patient
        patient_lab_values: list["PatientLabValue"] = []
        if self.patient:
            patient_lab_values = list(self.patient.lab_values.all())

        current_examination: list["Examination"] = []
        if self.examination:
            current_examination = [self.examination]

        # Now aggregate findings data from all PatientFinding instances
        findings_list: list["Finding"] = []
        finding_classifications_list: list["FindingClassification"] = []
        finding_classification_choices_list: list["FindingClassificationChoice"] = []
        finding_interventions_list: list["FindingIntervention"] = []
        patient_findings_list: list["PatientFinding"] = []

        for patient_finding in self.patient_findings.all():
            # Add the PatientFinding itself
            patient_findings_list.append(patient_finding)

            # Add the base Finding
            finding = cast(Finding | None, getattr(patient_finding, "finding", None))
            if finding is not None:
                findings_list.append(finding)

            # Add all active classifications and their choices from this PatientFinding
            active_classifications = cast(
                list[_PatientFindingClassificationLike],
                list(patient_finding.active_classifications),
            )
            for pf_classification in active_classifications:
                classification = pf_classification.classification
                if classification is not None:
                    finding_classifications_list.append(classification)
                classification_choice = pf_classification.classification_choice
                if classification_choice is not None:
                    finding_classification_choices_list.append(classification_choice)

            # Add all active interventions from this PatientFinding
            active_interventions = cast(
                list[_PatientFindingInterventionLike],
                list(patient_finding.active_interventions),
            )
            for pf_intervention in active_interventions:
                intervention = pf_intervention.intervention
                if intervention is not None:
                    finding_interventions_list.append(intervention)

        return ModelLinks(
            patient_examinations=[self],  # Add the instance itself
            examinations=current_examination,  # Add the related Examination model
            examination_indications=examination_indications_list,
            examination_indication_classification_choices=indication_choices_list,
            patient_lab_values=patient_lab_values,
            # Add findings-related data
            patient_findings=patient_findings_list,
            findings=findings_list,
            finding_classifications=finding_classifications_list,
            finding_classification_choices=finding_classification_choices_list,
            finding_interventions=finding_interventions_list,
        )

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
