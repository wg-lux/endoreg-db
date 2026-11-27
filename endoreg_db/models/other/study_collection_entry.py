"""Immutable typed observations and evaluations linked to existing study identities."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Unpack
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.study_collection import StudyCollectionRecord
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.clinical_reference import ClinicalReferenceReceipt

if TYPE_CHECKING:
    from endoreg_db.models.other.clinical_reference_import import (
        ClinicalReferenceImport,
    )
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination
    from endoreg_db.models.other.study import Study
    from endoreg_db.models.other.study_cohort import StudyCohort


class StudyCollectionEntry(models.Model):
    objects = models.Manager["StudyCollectionEntry"]()
    uuid: models.UUIDField[UUID, UUID] = models.UUIDField(
        default=uuid4, unique=True, editable=False
    )
    study: models.ForeignKey[Study] = models.ForeignKey(
        "Study", on_delete=models.PROTECT, related_name="collection_entries"
    )
    cohort: models.ForeignKey[StudyCohort | None] = models.ForeignKey(
        "StudyCohort",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="collection_entries",
    )
    patient_examination: models.ForeignKey[PatientExamination] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.PROTECT,
        related_name="study_collection_entries",
    )
    package_import: models.ForeignKey[ClinicalReferenceImport] = models.ForeignKey(
        "ClinicalReferenceImport",
        on_delete=models.PROTECT,
        related_name="study_collection_entries",
    )
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    def validated_record(self) -> StudyCollectionRecord:
        try:
            record = StudyCollectionRecord.model_validate_json(json.dumps(self.payload))
            receipt = ClinicalReferenceReceipt.model_validate(
                self.package_import.payload
            )
            if (
                receipt.package_snapshot is None
                or receipt.package_snapshot.snapshot_id != record.package_snapshot_id
            ):
                raise ValidationError("Collection package snapshot mismatch")
            if (
                record.record.knowledge_base_module != self.package_import.module
                or record.record.knowledge_base_version != self.package_import.version
            ):
                raise ValidationError("Collection package identity mismatch")
        except SchemaError as error:
            raise ValidationError("Invalid study collection record") from error
        cohort = self.cohort
        if (
            record.record_uuid != self.uuid
            or record.study_uuid != self.study.uuid
            or record.patient_examination_uuid != self.patient_examination.uuid
            or record.cohort_uuid != (cohort.definition_uuid if cohort else None)
        ):
            raise ValidationError("Study collection identity mismatch")
        if cohort is not None and (
            cohort.study_id != self.study.pk
            or cohort.created_by_id != self.study.created_by_id
        ):
            raise ValidationError(
                "Collection cohort must belong to the study and owner"
            )
        if (
            record.cohort_definition is not None
            and record.cohort_definition.study_id != self.study.pk
        ):
            raise ValidationError("Collection definition belongs to another study")
        examination = self.patient_examination
        if (
            record.record.patient != str(examination.patient_id)
            or record.record.examination != examination.examination_safe.name
        ):
            raise ValidationError("Collection ledger does not match its examination")
        return record

    def clean(self) -> None:
        super().clean()
        self.payload = self.validated_record().model_dump(mode="json")
        if self.pk is not None:
            raise ValidationError("Study collection entries are immutable")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)
