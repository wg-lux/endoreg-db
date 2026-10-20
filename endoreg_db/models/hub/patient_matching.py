"""Durable matching sessions and relationship edges, separate from patient rows."""

from __future__ import annotations

from typing import Unpack
from uuid import UUID

from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas.patient_matching import MatchingRequest, MatchingResult


class PatientMatchingBudget(models.Model):
    source_uuid: models.UUIDField[UUID, UUID] = models.UUIDField(primary_key=True)
    requests_used: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=0
    )


class PatientMatchingSession(models.Model):
    session_id: models.UUIDField[UUID, UUID] = models.UUIDField(primary_key=True)
    request_json: models.TextField[str, str] = models.TextField()
    completion_json: models.TextField[str, str] = models.TextField(default="")
    signatures_json: models.TextField[str, str] = models.TextField(default="")
    authorized_by: models.CharField[str, str] = models.CharField(max_length=255)
    completed_by: models.CharField[str, str] = models.CharField(
        max_length=255, default=""
    )
    consumed: models.BooleanField[bool, bool] = models.BooleanField(default=False)

    class Meta:
        permissions = [
            ("authorize_patient_matching", "Authorize bounded patient matching"),
            ("execute_patient_matching", "Record local patient matching computation"),
            ("accept_patient_matching", "Accept authenticated patient matching result"),
        ]

    def clean(self) -> None:
        super().clean()
        request = MatchingRequest.model_validate_json(self.request_json)
        if request.metadata.session_id != self.session_id:
            raise ValueError("Session request mismatch")
        if self.completion_json:
            result = MatchingResult.model_validate_json(self.completion_json)
            if result.metadata != request.metadata or result.source != request.source:
                raise ValueError("Completion context mismatch")
            if any(row not in request.candidates for row in result.matches):
                raise ValueError("Completion contains unauthorized candidate")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(**kwargs)


class PatientMatchingRelationship(models.Model):
    session: models.OneToOneField[PatientMatchingSession] = models.OneToOneField(
        PatientMatchingSession, primary_key=True, on_delete=models.PROTECT
    )
    source_uuid: models.UUIDField[UUID, UUID] = models.UUIDField()
    candidate_uuid: models.UUIDField[UUID, UUID] = models.UUIDField()
