from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
import yaml

from endoreg_db.import_files import video_import_service
from endoreg_db.models import Center, VideoFile
from endoreg_db.services import hls_media, video_processed_transcode
from endoreg_db.utils.file_operations import atomic_write_file
from workflow.scripts.import_common import (
    ImportReceipt,
    ReceiptProvenance,
    VideoHlsReceipt,
    VideoTranscodeReceipt,
)

pytestmark = pytest.mark.django_db

_SOURCE_GENERATION_V1 = "a" * 64
_PROCESSED_GENERATION_V1 = "b" * 64
_PROCESSED_GENERATION_V2 = "c" * 64
_STALE_GENERATION = "d" * 64
_CONFIG_SHA256 = "e" * 64


@dataclass(frozen=True)
class _WorkflowPaths:
    repository_root: Path
    video_import_script: Path
    video_transcode_script: Path
    video_hls_script: Path


class _NamedInput(list[str]):
    source: str

    def __init__(self, values: list[str], *, source: str = "") -> None:
        super().__init__(values)
        self.source = source


def _write_bytes(path: Path, content: bytes) -> None:
    atomic_write_file(
        destination=path,
        content=(content,),
        required_bytes=len(content),
    )


def _paths() -> _WorkflowPaths:
    repository_root = Path(__file__).resolve().parents[2]
    scripts = repository_root / "workflow/scripts"
    return _WorkflowPaths(
        repository_root=repository_root,
        video_import_script=scripts / "run_video_import.py",
        video_transcode_script=scripts / "run_video_transcode.py",
        video_hls_script=scripts / "run_video_hls_materialization.py",
    )


def _resources() -> dict[str, int]:
    return {
        "threads": 2,
        "mem_mb": 2048,
        "rust_workers": 2,
        "ffmpeg_threads": 2,
        "gpu": 0,
    }


def _receipt_provenance() -> ReceiptProvenance:
    completed_at = datetime(2026, 7, 28, 12, 0, 1, tzinfo=timezone.utc)
    return ReceiptProvenance(
        batch_id="failure-injection-batch",
        attempt=1,
        config_sha256=_CONFIG_SHA256,
        started_at=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
        completed_at=completed_at,
        duration_seconds=1.0,
    )


def _run_stage(
    script: Path,
    *,
    job_id: str,
    job: dict[str, object],
    inputs: _NamedInput,
    receipt: Path,
) -> None:
    snakemake = SimpleNamespace(
        input=inputs,
        output=SimpleNamespace(receipt=str(receipt)),
        params=SimpleNamespace(
            job=job,
            resources=_resources(),
            django_settings_module="endoreg_db.config.settings.test",
        ),
        wildcards={"job": job_id},
        threads=2,
    )
    runpy.run_path(
        str(script),
        init_globals={"snakemake": snakemake},
        run_name=f"__snakemake_test_{job_id}__",
    )


@pytest.fixture
def workflow_center() -> Center:
    return Center.objects.create(
        name="offline-workflow-center",
        display_name="Offline Workflow Center",
    )


def test_import_interruption_does_not_publish_success_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    receipt = tmp_path / "receipts/video/interrupted.json"
    _write_bytes(source, b"immutable test video generation")

    class InterruptedImportService:
        def import_and_anonymize(self, **_kwargs: object) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        video_import_service,
        "VideoImportService",
        InterruptedImportService,
    )

    with pytest.raises(KeyboardInterrupt):
        _run_stage(
            _paths().video_import_script,
            job_id="interrupted",
            job={
                "source": str(source),
                "center_name": "offline-workflow-center",
                "processor_name": "processor",
                "retry": False,
            },
            inputs=_NamedInput([str(source)], source=str(source)),
            receipt=receipt,
        )

    assert not receipt.exists()


def test_process_termination_during_import_does_not_publish_success_receipt(
    tmp_path: Path,
) -> None:
    paths = _paths()
    source = tmp_path / "source.mp4"
    receipt = tmp_path / "receipts/video/terminated.json"
    entered_service = tmp_path / "entered-service"
    _write_bytes(source, b"immutable terminated-process source")
    child_program = f"""
import runpy
import time
from types import SimpleNamespace

import django

django.setup()

from endoreg_db.import_files import video_import_service
from endoreg_db.utils.file_operations import atomic_write_file

class BlockingImportService:
    def import_and_anonymize(self, **_kwargs):
        marker = __import__("pathlib").Path({str(entered_service)!r})
        atomic_write_file(
            destination=marker,
            content=(b"entered",),
            required_bytes=7,
        )
        time.sleep(60)

video_import_service.VideoImportService = BlockingImportService
source = {str(source)!r}
snakemake = SimpleNamespace(
    input=SimpleNamespace(source=source),
    output=SimpleNamespace(receipt={str(receipt)!r}),
    params=SimpleNamespace(
        job={{
            "source": source,
            "center_name": "offline-workflow-center",
            "processor_name": "processor",
            "retry": False,
        }},
        resources={_resources()!r},
        django_settings_module="endoreg_db.config.settings.test",
    ),
    wildcards={{"job": "terminated"}},
    threads=2,
)
runpy.run_path(
    {str(paths.video_import_script)!r},
    init_globals={{"snakemake": snakemake}},
    run_name="__snakemake_termination_test__",
)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", child_program],
        cwd=paths.repository_root,
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 20
    try:
        while not entered_service.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("import subprocess did not enter the service boundary")
            time.sleep(0.05)
        assert process.poll() is None, process.stderr.read() if process.stderr else ""
        process.terminate()
        return_code = process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    assert return_code != 0
    assert not receipt.exists()


def test_hls_lease_contention_does_not_publish_success_receipt(
    tmp_path: Path,
    workflow_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = VideoFile.objects.create(
        center=workflow_center,
        video_hash="lease-contention-source",
        processed_video_hash="lease-contention-processed",
    )
    receipt = tmp_path / "receipts/video_hls/contended.json"

    def contended_materialization(
        video_id: int,
        *,
        artifact_kind: hls_media.HlsArtifactKind,
        force: bool,
    ) -> hls_media.HlsMaterializationResult:
        assert video_id == video.pk
        assert artifact_kind == "processed"
        assert force is False
        return hls_media.HlsMaterializationResult(
            video_id=video_id,
            artifact_kind=artifact_kind,
            status="already_materializing",
            key_id="",
            playlist_relative_path="",
            segment_directory_relative_path="",
            segment_count=0,
            detail="active lease owns materialization",
        )

    monkeypatch.setattr(
        hls_media,
        "materialize_video_hls",
        contended_materialization,
    )

    with pytest.raises(RuntimeError, match="already active"):
        _run_stage(
            _paths().video_hls_script,
            job_id="contended",
            job={
                "video_id": video.pk,
                "artifact_kind": "processed",
                "force": False,
            },
            inputs=_NamedInput([]),
            receipt=receipt,
        )

    assert not receipt.exists()


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (b"{not-json", "Invalid video import receipt schema"),
        (
            json.dumps(
                {
                    "schema_version": "0.9",
                    "job_id": "video-1",
                    "media_type": "video",
                    "source_sha256": "source",
                    "database_id": 1,
                    "content_hash": "source-generation",
                    "retry_requested": False,
                    "completed_at": "2026-07-28T12:00:00Z",
                }
            ).encode(),
            "Invalid video import receipt schema",
        ),
    ],
)
def test_transcode_rejects_malformed_import_receipt_before_service_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    expected_error: str,
) -> None:
    upstream = tmp_path / "receipts/video/video-1.json"
    receipt = tmp_path / "receipts/video_transcode/transcode-1.json"
    _write_bytes(upstream, payload)

    def unexpected_transcode(*_args: object, **_kwargs: object) -> None:
        pytest.fail("transcode service was called for an invalid receipt")

    monkeypatch.setattr(
        video_processed_transcode,
        "transcode_processed_video_for_storage_pressure",
        unexpected_transcode,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        _run_stage(
            _paths().video_transcode_script,
            job_id="transcode-1",
            job={
                "import_job": "video-1",
                "apply": True,
                "force_cpu": True,
            },
            inputs=_NamedInput([str(upstream)]),
            receipt=receipt,
        )

    assert not receipt.exists()


@pytest.mark.parametrize(
    ("upstream_kind", "expected_error"),
    [
        ("import", "current source generation"),
        ("transcode", "current processed video generation"),
    ],
)
def test_hls_rejects_stale_generation_before_service_invocation(
    tmp_path: Path,
    workflow_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    upstream_kind: str,
    expected_error: str,
) -> None:
    video = VideoFile.objects.create(
        center=workflow_center,
        video_hash=_SOURCE_GENERATION_V1,
        processed_video_hash=_PROCESSED_GENERATION_V1,
    )
    upstream = tmp_path / f"{upstream_kind}.json"
    receipt = tmp_path / "hls.json"
    if upstream_kind == "import":
        upstream_receipt = ImportReceipt(
            job_id="video-1",
            media_type="video",
            preflight_source_sha256="f" * 64,
            database_id=video.pk,
            published_content_sha256=_STALE_GENERATION,
            retry_requested=False,
            **_receipt_provenance().model_dump(),
        )
        job: dict[str, object] = {
            "import_job": "video-1",
            "artifact_kind": "processed",
        }
    else:
        upstream_receipt = VideoTranscodeReceipt(
            job_id="transcode-1",
            video_id=video.pk,
            status="changed",
            previous_processed_sha256=_PROCESSED_GENERATION_V1,
            candidate_processed_sha256=_STALE_GENERATION,
            published_processed_sha256=_STALE_GENERATION,
            old_size=20,
            new_size=10,
            detail="",
            **_receipt_provenance().model_dump(),
        )
        job = {
            "transcode_job": "transcode-1",
            "artifact_kind": "processed",
        }
    _write_bytes(
        upstream,
        f"{upstream_receipt.model_dump_json()}\n".encode(),
    )

    def unexpected_materialization(*_args: object, **_kwargs: object) -> None:
        pytest.fail("HLS service was called for a stale generation")

    monkeypatch.setattr(
        hls_media,
        "materialize_video_hls",
        unexpected_materialization,
    )

    with pytest.raises(RuntimeError, match=expected_error):
        _run_stage(
            _paths().video_hls_script,
            job_id="hls-1",
            job=job,
            inputs=_NamedInput([str(upstream)]),
            receipt=receipt,
        )

    assert not receipt.exists()


def test_stage_scripts_handoff_authoritative_generations_end_to_end(
    tmp_path: Path,
    workflow_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths()
    source = tmp_path / "source.mp4"
    import_receipt_path = tmp_path / "receipts/video/video-1.json"
    transcode_receipt_path = tmp_path / "receipts/video_transcode/transcode-1.json"
    hls_receipt_path = tmp_path / "receipts/video_hls/hls-1.json"
    _write_bytes(source, b"immutable integration source")
    video = VideoFile.objects.create(
        center=workflow_center,
        video_hash=_SOURCE_GENERATION_V1,
        processed_video_hash=_PROCESSED_GENERATION_V1,
    )

    class SuccessfulImportService:
        def import_and_anonymize(self, **_kwargs: object) -> VideoFile:
            return video

    def successful_transcode(
        selected_video: VideoFile,
        *,
        apply: bool,
        quality_mode: Literal["fast", "balanced", "quality"],
        force_cpu: bool,
        allow_larger: bool,
    ) -> video_processed_transcode.ProcessedVideoTranscodeResult:
        assert selected_video.pk == video.pk
        assert apply is True
        assert quality_mode == "balanced"
        assert force_cpu is True
        assert allow_larger is False
        selected_video.processed_video_hash = _PROCESSED_GENERATION_V2
        selected_video.save(update_fields=["processed_video_hash"])
        return video_processed_transcode.ProcessedVideoTranscodeResult(
            video_id=video.pk,
            status="changed",
            old_hash=_PROCESSED_GENERATION_V1,
            new_hash=_PROCESSED_GENERATION_V2,
            old_size=200,
            new_size=100,
            old_processed_name="old.mp4",
            new_processed_name="new.mp4",
            old_streamable_relative_path="",
            new_streamable_relative_path="",
            detail="validated generation replacement",
        )

    def successful_hls(
        video_id: int,
        *,
        artifact_kind: hls_media.HlsArtifactKind,
        force: bool,
    ) -> hls_media.HlsMaterializationResult:
        persisted = VideoFile.objects.get(pk=video_id)
        assert persisted.processed_video_hash == _PROCESSED_GENERATION_V2
        assert artifact_kind == "processed"
        assert force is False
        return hls_media.HlsMaterializationResult(
            video_id=video_id,
            artifact_kind=artifact_kind,
            status="materialized",
            key_id="test-key",
            playlist_relative_path="protected/hls/playlist.m3u8",
            segment_directory_relative_path="protected/hls/segments",
            segment_count=2,
            detail="validated and atomically published by service boundary",
        )

    monkeypatch.setattr(
        video_import_service,
        "VideoImportService",
        SuccessfulImportService,
    )
    monkeypatch.setattr(
        video_processed_transcode,
        "transcode_processed_video_for_storage_pressure",
        successful_transcode,
    )
    monkeypatch.setattr(
        hls_media,
        "materialize_video_hls",
        successful_hls,
    )

    _run_stage(
        paths.video_import_script,
        job_id="video-1",
        job={
            "source": str(source),
            "center_name": workflow_center.name,
            "processor_name": "processor",
            "retry": False,
        },
        inputs=_NamedInput([str(source)], source=str(source)),
        receipt=import_receipt_path,
    )
    import_receipt = ImportReceipt.model_validate_json(
        import_receipt_path.read_text(encoding="utf-8")
    )

    _run_stage(
        paths.video_transcode_script,
        job_id="transcode-1",
        job={
            "import_job": "video-1",
            "apply": True,
            "force_cpu": True,
        },
        inputs=_NamedInput([str(import_receipt_path)]),
        receipt=transcode_receipt_path,
    )
    transcode_receipt = VideoTranscodeReceipt.model_validate_json(
        transcode_receipt_path.read_text(encoding="utf-8")
    )

    _run_stage(
        paths.video_hls_script,
        job_id="hls-1",
        job={
            "transcode_job": "transcode-1",
            "artifact_kind": "processed",
        },
        inputs=_NamedInput([str(transcode_receipt_path)]),
        receipt=hls_receipt_path,
    )
    hls_receipt = VideoHlsReceipt.model_validate_json(
        hls_receipt_path.read_text(encoding="utf-8")
    )

    assert import_receipt.database_id == video.pk
    assert import_receipt.published_content_sha256 == _SOURCE_GENERATION_V1
    assert transcode_receipt.previous_processed_sha256 == _PROCESSED_GENERATION_V1
    assert transcode_receipt.published_processed_sha256 == _PROCESSED_GENERATION_V2
    assert hls_receipt.video_id == video.pk
    assert hls_receipt.source_generation_sha256 == _PROCESSED_GENERATION_V2
    assert hls_receipt.status == "materialized"
    assert hls_receipt.segment_count == 2


def test_production_snakefile_rulegraph_has_import_transcode_hls_path(
    tmp_path: Path,
) -> None:
    paths = _paths()
    source = tmp_path / "source.mp4"
    _write_bytes(source, b"rulegraph source")
    config = {
        "django_settings_module": "endoreg_db.config.settings.test",
        "receipt_directory": str(tmp_path / "receipts"),
        "resources": {
            "video": _resources(),
            "report": {
                "threads": 1,
                "mem_mb": 512,
                "rust_workers": 1,
                "ffmpeg_threads": 1,
                "gpu": 0,
            },
            "video_transcode": _resources(),
            "video_hls": _resources(),
        },
        "video_imports": {
            "video-1": {
                "source": str(source),
                "center_name": "offline-workflow-center",
                "processor_name": "processor",
            }
        },
        "report_imports": {},
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
    config_path = tmp_path / "imports.yaml"
    _write_bytes(
        config_path,
        yaml.safe_dump(config, sort_keys=True).encode(),
    )
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
            "--rulegraph",
            "--cores",
            "2",
        ],
        cwd=paths.repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "import_video" in result.stdout
    assert "transcode_processed_video" in result.stdout
    assert "materialize_video_hls" in result.stdout
    node_ids = {
        label: node_id
        for node_id, label in re.findall(
            r'^\s*(\d+)\[label = "([^"]+)"',
            result.stdout,
            flags=re.MULTILINE,
        )
    }
    edges = set(
        re.findall(r"^\s*(\d+) -> (\d+)\s*$", result.stdout, flags=re.MULTILINE)
    )
    assert (
        node_ids["import_video"],
        node_ids["transcode_processed_video"],
    ) in edges
    assert (
        node_ids["transcode_processed_video"],
        node_ids["materialize_video_hls"],
    ) in edges
