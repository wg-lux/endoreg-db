from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from typing import Callable, cast

import pytest

from endoreg_db.services import report_history
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientFindingClassificationHistoryData,
    PatientFindingInterventionHistoryData,
)

_serialize_patient_finding_classification = cast(
    Callable[[object], PatientFindingClassificationHistoryData],
    getattr(report_history, "_serialize_patient_finding_classification"),
)
_serialize_patient_finding_intervention = cast(
    Callable[[object], PatientFindingInterventionHistoryData],
    getattr(report_history, "_serialize_patient_finding_intervention"),
)


def test_report_history_serializes_temporal_and_json_values() -> None:
    intervention = SimpleNamespace(
        pk=7,
        intervention_id=11,
        intervention=SimpleNamespace(name="Polypectomy"),
        state="completed",
        date=date(2026, 8, 20),
        time_start=time(10, 15, 30),
        time_end=None,
    )
    classification = SimpleNamespace(
        pk=8,
        classification_id=12,
        classification_choice_id=13,
        classification=SimpleNamespace(name="Paris"),
        classification_choice=SimpleNamespace(name="0-Is"),
        subcategories={"reviewed_at": date(2026, 8, 20)},
        numerical_descriptors={"size_mm": 8},
    )

    assert _serialize_patient_finding_intervention(intervention) == {
        "id": 7,
        "intervention_id": 11,
        "intervention_name": "Polypectomy",
        "state": "completed",
        "date": "2026-08-20",
        "time_start": "10:15:30",
        "time_end": None,
    }
    assert _serialize_patient_finding_classification(classification) == {
        "id": 8,
        "classification_id": 12,
        "classification_choice_id": 13,
        "classification_name": "Paris",
        "classification_choice_name": "0-Is",
        "subcategories": {"reviewed_at": "2026-08-20"},
        "numerical_descriptors": {"size_mm": 8},
    }


def test_report_history_rejects_non_object_json_fields() -> None:
    classification = SimpleNamespace(
        pk=8,
        classification_id=None,
        classification_choice_id=None,
        classification=None,
        classification_choice=None,
        subcategories=["not", "an", "object"],
        numerical_descriptors={},
    )

    with pytest.raises(ValueError, match="must contain an object"):
        _serialize_patient_finding_classification(classification)
