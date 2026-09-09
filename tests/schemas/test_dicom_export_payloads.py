from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.dicom_export import validate_dicom_export_manifest_v2


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "dicom_manifest_v2_existing.json"
)
FieldMutation = Literal[
    "identity_not_removed",
    "invalid_study_uid",
    "invalid_artifact_sha256",
    "unsafe_artifact_reference",
    "naive_created_at",
]
UniquenessMutation = Literal[
    "duplicate_instance_in_series",
    "duplicate_series_in_study",
    "duplicate_instance_across_series",
]


def _manifest() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(FIXTURE_PATH.read_text(encoding="utf-8")),
    )


def _study(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["study"])


def _series(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], _study(payload)["series"])


def _first_instance(payload: dict[str, object]) -> dict[str, object]:
    instances = cast(list[dict[str, object]], _series(payload)[0]["instances"])
    return instances[0]


def _apply_field_mutation(
    payload: dict[str, object],
    mutation: FieldMutation,
) -> None:
    if mutation == "identity_not_removed":
        deidentification = cast(dict[str, object], payload["deidentification"])
        deidentification["patient_identity_removed"] = False
    elif mutation == "invalid_study_uid":
        _study(payload)["study_instance_uid"] = "invalid"
    elif mutation == "invalid_artifact_sha256":
        _first_instance(payload)["artifact_sha256"] = "not-a-sha256"
    elif mutation == "unsafe_artifact_reference":
        _first_instance(payload)["artifact_reference"] = "../escape.dcm"
    else:
        payload["created_at"] = "2026-07-17T12:00:00"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("identity_not_removed", "patient_identity_removed must be true"),
        ("invalid_study_uid", "DICOM UIDs"),
        ("invalid_artifact_sha256", "SHA-256 hex digest"),
        ("unsafe_artifact_reference", "relative protected-storage key"),
        ("naive_created_at", "timezone-aware"),
    ],
)
def test_dicom_manifest_rejects_invalid_deidentification_and_artifact_fields(
    mutation: FieldMutation,
    message: str,
) -> None:
    payload = _manifest()
    _apply_field_mutation(payload, mutation)

    with pytest.raises(ValueError, match=message):
        validate_dicom_export_manifest_v2(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_instance_in_series", "unique within a series"),
        ("duplicate_series_in_study", "unique within a study"),
        ("duplicate_instance_across_series", "unique within an export"),
    ],
)
def test_dicom_manifest_rejects_duplicate_uids_at_each_scope(
    mutation: UniquenessMutation,
    message: str,
) -> None:
    payload = _manifest()
    series = _series(payload)
    instances = cast(list[dict[str, object]], series[0]["instances"])

    if mutation == "duplicate_instance_in_series":
        instances.append(deepcopy(instances[0]))
    else:
        duplicate_series = deepcopy(series[0])
        if mutation == "duplicate_instance_across_series":
            duplicate_series["series_instance_uid"] = "2.25.2002"
        series.append(duplicate_series)

    with pytest.raises(ValueError, match=message):
        validate_dicom_export_manifest_v2(payload)


def test_dicom_manifest_rejects_non_mapping_payload() -> None:
    with pytest.raises(ValueError) as caught:
        validate_dicom_export_manifest_v2(42)

    assert isinstance(caught.value.__cause__, ValidationError)
