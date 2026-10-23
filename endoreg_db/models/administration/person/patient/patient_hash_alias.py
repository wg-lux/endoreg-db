"""Historical pseudonyms retained at the owning center across salt migration."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Unpack, cast

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from endoreg_db.helpers.typing import DjangoModelSaveKwargs

from endoreg_db.models.administration.center.center import Center
from .patient import Patient


class PatientHashAlias(models.Model):
    center: models.ForeignKey[Center] = models.ForeignKey(
        "Center", on_delete=models.PROTECT
    )
    patient: models.ForeignKey[Patient] = models.ForeignKey(
        "Patient", on_delete=models.PROTECT
    )
    patient_hash: models.CharField[str, str] = models.CharField(
        max_length=64, validators=[RegexValidator(r"\A[0-9a-f]{64}\Z")]
    )
    salt_fingerprint: models.CharField[str, str] = models.CharField(
        max_length=64, validators=[RegexValidator(r"\A[0-9a-f]{64}\Z")]
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    objects = cast(models.Manager["PatientHashAlias"], models.Manager())

    if TYPE_CHECKING:
        center_id: int
        patient_id: int

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("center", "patient_hash"),
                name="unique_patient_hash_alias_center",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.patient_id and self.patient.center_id != self.center_id:
            raise ValidationError("Historical identity belongs to a different center")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)
