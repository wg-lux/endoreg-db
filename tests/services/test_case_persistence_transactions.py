from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import uuid4

import pytest
from django.db import models
from django.utils import timezone

from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.serializers import case as case_serializer_module
from endoreg_db.serializers.case import CaseSerializer
from endoreg_db.services.cases import persist_case_graph


def _patient() -> Patient:
    return Patient.objects.create(
        patient_hash=f"case-transaction-{uuid4().hex}",
        first_name="Case",
        last_name="Transaction",
    )


@pytest.mark.django_db
def test_relationship_failure_rolls_back_scalar_case_update() -> None:
    patient = _patient()
    patient_case = Case.objects.create(
        patient=patient,
        start_date=timezone.now(),
        hash="original",
    )
    unsaved_examination = PatientExamination(
        patient=patient,
        hash=f"unsaved-examination-{uuid4().hex}",
    )

    with pytest.raises(ValueError):
        persist_case_graph(
            instance=patient_case,
            scalar_values={"patient": patient, "hash": "must-roll-back"},
            relationships={"patient_examinations": [unsaved_examination]},
        )

    patient_case.refresh_from_db()
    assert patient_case.hash == "original"
    assert patient_case.patient_examinations.count() == 0


@pytest.mark.django_db
def test_partial_serializer_update_does_not_rewrite_omitted_relationships(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient = _patient()
    patient_case = Case.objects.create(
        patient=patient,
        start_date=timezone.now(),
        hash="original",
    )
    captured_relationships: Mapping[str, Sequence[models.Model]] | None = None

    def capture_persist(
        *,
        instance: Case | None,
        scalar_values: Mapping[str, object],
        relationships: Mapping[str, Sequence[models.Model]],
    ) -> Case:
        del scalar_values
        nonlocal captured_relationships
        captured_relationships = relationships
        assert instance is not None
        return instance

    monkeypatch.setattr(
        case_serializer_module,
        "persist_case_graph",
        capture_persist,
    )
    serializer = CaseSerializer(
        instance=patient_case,
        data={"hash": "updated"},
        partial=True,
    )

    assert serializer.is_valid()
    saved_case = serializer.save()

    assert saved_case == patient_case
    assert captured_relationships == {}


@pytest.mark.django_db
def test_scalar_update_preserves_existing_relationships() -> None:
    patient = _patient()
    examination = PatientExamination.objects.create(
        patient=patient,
        hash=f"case-transaction-examination-{uuid4().hex}",
    )
    patient_case = Case.objects.create(
        patient=patient,
        start_date=timezone.now(),
        hash="original",
    )
    patient_case.patient_examinations.add(examination)

    updated = persist_case_graph(
        instance=patient_case,
        scalar_values={"patient": patient, "hash": "updated"},
        relationships={},
    )

    assert updated.hash == "updated"
    assert list(updated.patient_examinations.values_list("pk", flat=True)) == [
        cast(Any, examination).pk
    ]
