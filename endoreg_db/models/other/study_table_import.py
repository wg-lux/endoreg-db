"""Immutable, schema-validated provenance for an imported study row."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.table_import import TableImportReceiptPayload
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination


class StudyTableImport(models.Model):
    objects = models.Manager["StudyTableImport"]()
    if TYPE_CHECKING:
        center_id: int

    owner: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    center: models.ForeignKey[Center] = models.ForeignKey(
        "Center", on_delete=models.PROTECT
    )
    patient_examination: models.ForeignKey[PatientExamination | None] = (
        models.ForeignKey(
            "PatientExamination", on_delete=models.SET_NULL, null=True, blank=True
        )
    )
    deletion_record: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    owns_examination: models.BooleanField[bool, bool] = models.BooleanField(
        default=False
    )

    mapping_id: models.UUIDField[UUID, UUID] = models.UUIDField()
    source_id: models.UUIDField[UUID, UUID] = models.UUIDField()
    mapping_hash: models.CharField[str, str] = models.CharField(max_length=64)
    source_hash: models.CharField[str, str] = models.CharField(max_length=64)
    payload: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "center", "mapping_id", "source_id"],
                name="unique_study_table_import_row",
            )
        ]

    def clean(self) -> None:
        super().clean()
        try:
            payload = TableImportReceiptPayload.model_validate(self.payload)
            if self.deletion_record:
                baseline = DtypesRecordPersistencePayload.model_validate(
                    self.deletion_record
                )
                if (
                    baseline.patient != payload.record.patient
                    or baseline.examination != payload.record.examination
                ):
                    raise ValidationError(
                        "Deletion baseline identity differs from import receipt"
                    )
        except SchemaError as error:
            raise ValidationError("Invalid table import receipt") from error
        if self.owns_examination and not self.deletion_record:
            raise ValidationError(
                "Import ownership requires a validated deletion baseline"
            )
        examination = self.patient_examination
        if examination is None:
            raise ValidationError("New receipts require an examination")
        if (
            payload.record.patient != str(examination.patient_id)
            or payload.patient_hash != examination.patient.patient_hash
            or payload.examination_hash != examination.hash
            or examination.patient.center_id != self.center_id
        ):
            raise ValidationError(
                "Table receipt identity does not match its examination"
            )
        if self.pk is not None:
            raise ValidationError("Table import receipts are immutable")

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)
