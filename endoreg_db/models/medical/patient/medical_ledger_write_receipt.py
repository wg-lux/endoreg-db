from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.schemas.medical_ledger import MedicalLedgerRecordIds


class MedicalLedgerWriteReceipt(models.Model):
    """Durable idempotency identity for one committed medical aggregate write."""

    patient: models.ForeignKey[Any] = models.ForeignKey(
        "Patient",
        on_delete=models.CASCADE,
        related_name="medical_ledger_write_receipts",
    )
    idempotency_key: models.CharField[str, Any] = models.CharField(max_length=255)
    request_hash: models.CharField[str, Any] = models.CharField(max_length=64)
    record_ids: models.JSONField[dict[str, object], Any] = models.JSONField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[models.Manager["MedicalLedgerWriteReceipt"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        models.Manager()
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("patient", "idempotency_key"),
                name="medled_receipt_patient_key_uq",
            ),
            models.CheckConstraint(
                condition=~models.Q(idempotency_key=""),
                name="medled_receipt_key_nonempty",
            ),
            models.CheckConstraint(
                condition=~models.Q(request_hash=""),
                name="medled_receipt_hash_nonempty",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        try:
            record_ids = MedicalLedgerRecordIds.model_validate(self.record_ids)
        except PydanticValidationError as exc:
            raise ValidationError({"record_ids": str(exc)}) from exc
        self.record_ids = record_ids.model_dump(mode="json")

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)
