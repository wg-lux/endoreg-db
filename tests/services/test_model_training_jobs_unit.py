from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from endoreg_db.services.jobs import model_training_jobs as training


def test_coerce_uuid_returns_none_for_invalid_payload() -> None:
    assert training._coerce_uuid("not-a-uuid") is None
    assert training._coerce_uuid("12345678-1234-1234-1234-1234567890ab") is None


def test_coerce_uuid_accepts_uuid_string() -> None:
    parsed = training._coerce_uuid("550e8400-e29b-41d4-a716-446655440000")
    assert str(parsed) == "550e8400-e29b-41d4-a716-446655440000"


def test_parse_model_training_result_reads_last_json_object_line() -> None:
    output = "\n".join(
        [
            "INFO: starting",
            '{"step": "init"}',
            "WARN: mid",
            '{"artifact": "ready", "count": 3}',
        ]
    )
    parsed = training._parse_model_training_result(output)
    assert isinstance(parsed, dict)
    assert parsed["artifact"] == "ready"
    assert parsed["count"] == 3


def test_parse_model_training_result_returns_none_when_no_json() -> None:
    assert training._parse_model_training_result("INFO only\nWARN") is None


def test_model_training_artifact_paths_prefers_specific_artifacts() -> None:
    artifact_paths = training._model_training_artifact_paths(
        {
            "model_path": "/tmp/model.pt",
            "manifest_path": "/tmp/manifest.json",
            "training_result": {
                "artifacts": [
                    {"kind": "ONNX", "path": "/tmp/model.onnx"},
                    {"kind": "report", "path": "/tmp/report.txt"},
                ]
            },
        }
    )
    assert artifact_paths["model_path"] == "/tmp/model.pt"
    assert artifact_paths["manifest_path"] == "/tmp/manifest.json"
    assert artifact_paths["onnx_path"] == "/tmp/model.onnx"
    assert artifact_paths["report_path"] == "/tmp/report.txt"


def test_model_training_artifact_paths_safely_ignores_non_strings() -> None:
    artifact_paths = training._model_training_artifact_paths(
        {
            "model_path": None,
            "manifest_path": 123,
            "training_result": {"artifacts": [{"kind": "", "path": ""}, None]},
        }
    )
    assert artifact_paths == {}


def test_model_training_artifact_paths_ignores_invalid_artifact_block() -> None:
    artifact_paths = training._model_training_artifact_paths(
        {"training_result": {"artifacts": [{"kind": None, "path": 7}, None, 5]}}
    )
    assert artifact_paths == {}


def test_direct_training_run_update_validates_before_querying() -> None:
    run_uuid = UUID("550e8400-e29b-41d4-a716-446655440000")

    with patch.object(training.AIModelTrainingRun.objects, "filter") as filter_mock:
        with pytest.raises(ValueError, match="does not allow NaN"):
            training._update_model_training_run(
                run_uuid,
                result={"loss": float("nan")},
            )

    filter_mock.assert_not_called()


def test_direct_training_run_update_canonicalizes_all_json_fields() -> None:
    run_uuid = UUID("550e8400-e29b-41d4-a716-446655440000")
    completed_at = datetime(2026, 7, 31, 8, 30, tzinfo=timezone.utc)

    with patch.object(training.AIModelTrainingRun.objects, "filter") as filter_mock:
        filter_mock.return_value.update.return_value = 1
        updated = training._update_model_training_run(
            run_uuid,
            request_payload={"training_target": "image_multilabel"},
            command_kwargs={"output_dir": Path("/tmp/training")},
            result={"completed_at": completed_at},
            artifact_paths={"model_path": Path("/tmp/training/model.pt")},
        )

    assert updated == 1
    filter_mock.assert_called_once_with(run_id=run_uuid)
    filter_mock.return_value.update.assert_called_once_with(
        request_payload={"training_target": "image_multilabel"},
        command_kwargs={"output_dir": "/tmp/training"},
        result={"completed_at": "2026-07-31T08:30:00+00:00"},
        artifact_paths={"model_path": "/tmp/training/model.pt"},
    )


def test_expected_frame_relative_path_zero_padded() -> None:
    assert training._expected_frame_relative_path(5, "png") == "frame_0000005.png"
    assert training._expected_frame_relative_path(123) == "frame_0000123.jpg"


def test_consecutive_ranges_compresses_runs() -> None:
    assert training._consecutive_ranges([1, 2, 3, 7, 8, 10]) == [
        (1, 4),
        (7, 9),
        (10, 11),
    ]
    assert training._consecutive_ranges([]) == []


def test_merge_frame_intervals_merges_overlapping_and_adjacent() -> None:
    assert training._merge_frame_intervals([(1, 3), (5, 8), (2, 6), (8, 10)]) == [
        (1, 10),
    ]


def test_model_training_staging_root_defaults_to_setting() -> None:
    assert isinstance(training._model_training_staging_root(), Path)
    assert training.DEFAULT_MODEL_TRAINING_STAGING_ROOT == Path(
        "/mnt/fast-nvme-cache/endoreg-training"
    )
