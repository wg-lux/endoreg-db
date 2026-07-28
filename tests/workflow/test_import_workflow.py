from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import ValidationError

from endoreg_db.utils.file_operations import atomic_write_file
from workflow.scripts.import_common import WorkflowConfig


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
        "resources": {
            "video": {"threads": 2, "mem_mb": 1024},
            "report": {"threads": 1, "mem_mb": 512},
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


def test_snakemake_dry_run_builds_both_import_jobs(tmp_path: Path) -> None:
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

    assert result.returncode == 0, result.stderr
    assert "import_video" in result.stdout
    assert "import_report" in result.stdout

    expected_receipts = {
        str(tmp_path / "receipts/video/video-1.json"),
        str(tmp_path / "receipts/report/report-1.json"),
    }
    assert expected_receipts <= set(result.stdout.split())


def test_receipt_config_is_json_serializable(tmp_path: Path) -> None:
    parsed = WorkflowConfig.model_validate(_workflow_config(tmp_path))

    serialized = json.dumps(parsed.model_dump(mode="json"))

    assert '"video-1"' in serialized
