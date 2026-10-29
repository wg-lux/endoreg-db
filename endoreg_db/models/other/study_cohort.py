from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4
from typing import TYPE_CHECKING, Unpack

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination
    from endoreg_db.models.other.study import Study

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from lx_dtypes.models.contracts.json_types import JsonObject


class StudyCohort(models.Model):
    """User-owned cohort definition with shared dataset references, not copied annotations."""

    objects = models.Manager["StudyCohort"]()

    if TYPE_CHECKING:
        created_by_id: int
        study_id: int | None

    study: models.ForeignKey[Study | None] = models.ForeignKey(
        "endoreg_db.Study",
        on_delete=models.CASCADE,
        related_name="cohorts",
        null=True,
        blank=True,
    )

    name: models.CharField[str, str] = models.CharField(max_length=255)
    hypothesis: models.TextField[str, str] = models.TextField()
    filters: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    definition_snapshot: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    definition_uuid: models.UUIDField[UUID, UUID] = models.UUIDField(
        default=uuid4, unique=True, editable=False
    )
    study_metadata: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    datasets: models.ManyToManyField[AIDataSet, models.Model] = models.ManyToManyField(
        "endoreg_db.AIDataSet", related_name="study_cohorts", blank=True
    )
    imported_examinations: models.ManyToManyField[PatientExamination, models.Model] = (
        models.ManyToManyField(
            "endoreg_db.PatientExamination",
            related_name="imported_study_cohorts",
            blank=True,
        )
    )
    created_by: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    def clean(self) -> None:
        from lx_dtypes.models.contracts.study_setup import StudySetupDefinition
        from pydantic import ValidationError as SchemaError

        from lx_dtypes.models.contracts.cohort_definition import CohortDefinition

        super().clean()
        if (
            self.study_id is not None
            and self.study is not None
            and self.study.created_by_id != self.created_by_id
        ):
            raise ValidationError("Study and cohort must have the same owner.")
        try:
            definition = CohortDefinition.model_validate(
                {
                    "name": self.name,
                    "hypothesis": self.hypothesis,
                    "filters": self.filters,
                    "study_metadata": self.study_metadata,
                }
            )
            if self.definition_snapshot != {}:
                self.definition_snapshot = StudySetupDefinition.model_validate(
                    self.definition_snapshot
                ).model_dump(mode="json")
        except SchemaError as error:
            raise ValidationError("Invalid study cohort definition.") from error
        self.name = definition.name
        self.hypothesis = definition.hypothesis
        self.filters = definition.filters.model_dump(mode="json")
        self.study_metadata = definition.study_metadata.model_dump(mode="json")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)

    class Meta:
        ordering = ["name", "pk"]
