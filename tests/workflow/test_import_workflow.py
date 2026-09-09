from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from stat import S_IMODE
from typing import cast

import pytest
import yaml
from pydantic import ValidationError

from endoreg_db.utils.file_operations import atomic_write_file
from workflow.scripts.import_common import (
    ResolvedVideoReference,
    ImportReceipt,
    ReceiptProvenance,
    RuleResources,
    WorkflowConfig,
    assert_video_reference_is_current,
    configure_stage_threads,
    read_upstream_video_reference,
    require_source,
    stage_lifecycle,
)


def _write_bytes(path: Path, content: bytes) -> None:
    atomic_write_file(
        destination=path,
        content=(content,),
        required_bytes=len(content),
    )


def _workflow_config(tmp_path: Path) -> dict[str, object]:
    return {
        "django_settings_module": None,
        "receipt_directory": str(tmp_path / "receipts"),
        "log_directory": str(tmp_path / "logs"),
        "batch_id": "batch-20260728",
        "resources": {
            "video": {
                "threads": 2,
                "mem_mb": 1024,
                "rust_workers": 2,
                "ffmpeg_threads": 2,
                "gpu": 0,
            },
            "report": {
                "threads": 1,
                "mem_mb": 512,
                "rust_workers": 1,
                "ffmpeg_threads": 1,
                "gpu": 0,
            },
            "video_transcode": {
                "threads": 2,
                "mem_mb": 2048,
                "rust_workers": 2,
                "ffmpeg_threads": 2,
                "gpu": 0,
            },
            "video_hls": {
                "threads": 2,
                "mem_mb": 2048,
                "rust_workers": 2,
                "ffmpeg_threads": 2,
                "gpu": 0,
            },
        },
        "video_imports": {
            "video-1": {
                "source": str(tmp_path / "source.mp4"),
                "center_name": "center",
                "processor_name": "processor",
                "retry": False,
            }
        },
        "report_imports": {
            "report-1": {
                "source": str(tmp_path / "source.pdf"),
                "center_name": "center",
                "retry": False,
            }
        },
        "video_transcodes": {
            "transcode-1": {
                "import_job": "video-1",
                "apply": True,
                "force_cpu": True,
            }
        },
        "video_hls_materializations": {
            "hls-1": {
                "transcode_job": "transcode-1",
                "artifact_kind": "processed",
            }
        },
    }


def test_workflow_config_rejects_unsafe_job_identifier(tmp_path: Path) -> None:
    raw_config = _workflow_config(tmp_path)
    video_imports = cast(
        dict[str, dict[str, object]],
        raw_config["video_imports"],
    )
    video_imports["../escape"] = video_imports.pop("video-1")

    with pytest.raises(ValidationError, match="invalid import job identifiers"):
        WorkflowConfig.model_validate(raw_config)


def test_workflow_config_rejects_unsupported_report_suffix(
    tmp_path: Path,
) -> None:
    raw_config = _workflow_config(tmp_path)
    report_imports = cast(
        dict[str, dict[str, object]],
        raw_config["report_imports"],
    )
    report_job = report_imports["report-1"]
    report_job["source"] = str(tmp_path / "source.csv")

    with pytest.raises(ValidationError, match=r"must have a \.pdf or \.txt suffix"):
        WorkflowConfig.model_validate(raw_config)


def test_workflow_config_rejects_unknown_stage_reference(tmp_path: Path) -> None:
    raw_config = _workflow_config(tmp_path)
    transcodes = cast(
        dict[str, dict[str, object]],
        raw_config["video_transcodes"],
    )
    transcodes["transcode-1"]["import_job"] = "missing"

    with pytest.raises(ValidationError, match="unknown video import job references"):
        WorkflowConfig.model_validate(raw_config)


def test_workflow_config_rejects_hls_after_dry_run_transcode(
    tmp_path: Path,
) -> None:
    raw_config = _workflow_config(tmp_path)
    transcodes = cast(
        dict[str, dict[str, object]],
        raw_config["video_transcodes"],
    )
    transcodes["transcode-1"]["apply"] = False

    with pytest.raises(
        ValidationError,
        match="HLS jobs cannot depend on non-applying video transcodes",
    ):
        WorkflowConfig.model_validate(raw_config)


def test_require_source_rejects_symbolic_link(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    link = tmp_path / "link.mp4"
    link.symlink_to(source)

    with pytest.raises(RuntimeError, match="must not be a symbolic link"):
        require_source(link, str(link))


def test_rule_resources_reject_oversubscribed_rayon_pool() -> None:
    with pytest.raises(ValidationError, match="rust_workers cannot exceed threads"):
        RuleResources.model_validate(
            {
                "threads": 2,
                "mem_mb": 1024,
                "rust_workers": 3,
            }
        )


def test_stage_threads_cap_inner_pools_to_snakemake_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in (
        "RAYON_NUM_THREADS",
        "LX_ANNOTATE_HLS_FFMPEG_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
    ):
        monkeypatch.setenv(variable, "test-sentinel")
    resources = RuleResources(
        threads=4,
        mem_mb=16000,
        rust_workers=4,
        ffmpeg_threads=3,
        gpu=1,
    )

    worker_count = configure_stage_threads(
        allocated_threads=2,
        resources=resources,
    )

    assert worker_count == 2
    assert os.environ["RAYON_NUM_THREADS"] == "2"
    assert os.environ["LX_ANNOTATE_HLS_FFMPEG_THREADS"] == "2"
    assert os.environ["OMP_NUM_THREADS"] == "2"
    assert os.environ["MKL_NUM_THREADS"] == "2"


def test_upstream_import_receipt_resolves_video_id(tmp_path: Path) -> None:
    receipt = tmp_path / "import.json"
    payload = {
        "schema_version": "1.1",
        "batch_id": "batch-20260728",
        "attempt": 1,
        "config_sha256": "c" * 64,
        "started_at": "2026-07-28T11:59:00Z",
        "job_id": "video-1",
        "media_type": "video",
        "preflight_source_sha256": "a" * 64,
        "database_id": 42,
        "published_content_sha256": "b" * 64,
        "retry_requested": False,
        "completed_at": "2026-07-28T12:00:00Z",
        "duration_seconds": 60.0,
    }
    _write_bytes(
        receipt,
        f"{json.dumps(payload)}\n".encode(),
    )

    reference = read_upstream_video_reference(
        [str(receipt)],
        import_job="video-1",
        transcode_job=None,
    )

    assert reference.video_id == 42
    assert reference.source_video_hash == "b" * 64


def test_upstream_receipt_rejects_wrong_job_identity(tmp_path: Path) -> None:
    receipt = tmp_path / "import.json"
    payload = {
        "schema_version": "1.1",
        "batch_id": "batch-20260728",
        "attempt": 1,
        "config_sha256": "c" * 64,
        "started_at": "2026-07-28T11:59:00Z",
        "job_id": "other-job",
        "media_type": "video",
        "preflight_source_sha256": "a" * 64,
        "database_id": 42,
        "published_content_sha256": "b" * 64,
        "retry_requested": False,
        "completed_at": "2026-07-28T12:00:00Z",
        "duration_seconds": 60.0,
    }
    _write_bytes(receipt, f"{json.dumps(payload)}\n".encode())

    with pytest.raises(RuntimeError, match="stage or job identity"):
        read_upstream_video_reference(
            [str(receipt)],
            import_job="video-1",
            transcode_job=None,
        )


def test_upstream_receipt_rejects_stale_database_generation() -> None:
    class PersistedVideo:
        video_hash: str = "current"
        processed_video_hash: str | None = "processed"

    with pytest.raises(RuntimeError, match="current source generation"):
        assert_video_reference_is_current(
            PersistedVideo(),
            ResolvedVideoReference(
                video_id=42,
                source_video_hash="stale",
            ),
        )


def test_snakemake_dry_run_builds_video_lifecycle_and_report_jobs(
    tmp_path: Path,
) -> None:
    raw_config = _workflow_config(tmp_path)
    _write_bytes(tmp_path / "source.mp4", b"dry-run video input")
    _write_bytes(tmp_path / "source.pdf", b"%PDF-1.4\n%%EOF\n")

    config_payload = yaml.safe_dump(raw_config, sort_keys=True).encode("utf-8")
    config_path = tmp_path / "imports.yaml"
    _write_bytes(config_path, config_payload)

    repository_root = Path(__file__).resolve().parents[2]
    snakemake_executable = Path(sys.executable).with_name("snakemake")
    result = subprocess.run(
        [
            str(snakemake_executable),
            "--snakefile",
            "workflow/Snakefile",
            "--configfile",
            str(config_path),
            "--profile",
            "workflow/profiles/offline-batch",
            "--dry-run",
            "--cores",
            "2",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "import_video" in result.stdout
    assert "import_report" in result.stdout
    assert "transcode_processed_video" in result.stdout
    assert "materialize_video_hls" in result.stdout

    expected_receipts = {
        str(tmp_path / "receipts/video/video-1.json"),
        str(tmp_path / "receipts/report/report-1.json"),
        str(tmp_path / "receipts/video_transcode/transcode-1.json"),
        str(tmp_path / "receipts/video_hls/hls-1.json"),
    }
    assert expected_receipts <= set(result.stdout.split())
    assert str(tmp_path / "logs/batch-20260728/video/video-1.json") in result.stdout


def test_receipt_config_is_json_serializable(tmp_path: Path) -> None:
    parsed = WorkflowConfig.model_validate(_workflow_config(tmp_path))

    serialized = json.dumps(parsed.model_dump(mode="json"))

    assert '"video-1"' in serialized


def test_receipt_rejects_non_sha256_and_non_utc_provenance() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        ImportReceipt(
            job_id="video-1",
            media_type="video",
            preflight_source_sha256="not-a-sha256",
            database_id=42,
            published_content_sha256="b" * 64,
            retry_requested=False,
            batch_id="batch-1",
            attempt=1,
            config_sha256="c" * 64,
            started_at=datetime(2026, 7, 28, 12),
            completed_at=datetime(2026, 7, 28, 13, tzinfo=timezone.utc),
            duration_seconds=3600,
        )


def test_configuration_sha256_excludes_batch_identity(tmp_path: Path) -> None:
    first = WorkflowConfig.model_validate(_workflow_config(tmp_path))
    second_payload = _workflow_config(tmp_path)
    second_payload["batch_id"] = "another-batch"
    second = WorkflowConfig.model_validate(second_payload)

    assert first.configuration_sha256() == second.configuration_sha256()


def test_receipt_provenance_rejects_completed_before_started() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        ReceiptProvenance(
            batch_id="batch-1",
            attempt=1,
            config_sha256="c" * 64,
            started_at=datetime(2026, 7, 28, 13, tzinfo=timezone.utc),
            completed_at=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
            duration_seconds=1,
        )


def test_stage_lifecycle_log_is_private_and_records_success(tmp_path: Path) -> None:
    log_path = tmp_path / "batch-1/video/job-1.json"

    with stage_lifecycle(
        path=log_path,
        stage="video_import",
        job_id="job-1",
        batch_id="batch-1",
        attempt=2,
        config_sha256="c" * 64,
    ):
        started_payload = json.loads(log_path.read_text(encoding="utf-8"))
        assert [event["event"] for event in started_payload] == ["stage_started"]

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert [event["event"] for event in payload] == [
        "stage_started",
        "stage_succeeded",
    ]
    assert S_IMODE(log_path.stat().st_mode) == 0o600
    assert S_IMODE(log_path.parent.stat().st_mode) == 0o700


def test_stage_lifecycle_log_records_redacted_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "batch-1/video/job-1.json"

    with pytest.raises(RuntimeError, match="sensitive/source.mp4"):
        with stage_lifecycle(
            path=log_path,
            stage="video_import",
            job_id="job-1",
            batch_id="batch-1",
            attempt=1,
            config_sha256="c" * 64,
        ):
            raise RuntimeError("failure at /sensitive/source.mp4")

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert [event["event"] for event in payload] == [
        "stage_started",
        "stage_failed",
    ]
    assert payload[-1]["error_type"] == "RuntimeError"
    assert "sensitive" not in log_path.read_text(encoding="utf-8")
