from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUntypedFunctionDecorator=false

import json
import os
import subprocess
import sys
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from endoreg_db.config.env import (
    BASE_DIR,
    DATA_DIR_ENV,
    DJANGO_SETTINGS_MODULE_ENV,
    PROTECTED_MEDIA_ROOT_ENV,
    PROTECTED_ROOT_ENV,
    STORAGE_DIR_ENV,
)
from endoreg_db.management.commands import kcache_video_import as command_module
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import EndoregPathsModel

pytestmark = pytest.mark.django_db


@pytest.fixture
def isolated_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> EndoregPathsModel:
    protected_root = tmp_path / "protected"
    storage_root = protected_root / "storage"
    data_root = tmp_path / "data"
    monkeypatch.setenv(PROTECTED_ROOT_ENV, str(protected_root))
    monkeypatch.setenv(STORAGE_DIR_ENV, str(storage_root))
    monkeypatch.setenv(PROTECTED_MEDIA_ROOT_ENV, str(storage_root))
    monkeypatch.setenv(DATA_DIR_ENV, str(data_root))
    return EndoregPathsModel.from_environment()


@pytest.fixture
def command_center() -> Center:
    return Center.objects.create(
        name="kcache-command-center",
        display_name="KCache Command Center",
    )


@pytest.fixture
def command_processor(command_center: Center) -> EndoscopyProcessor:
    processor = EndoscopyProcessor.objects.create(name="kcache-command-processor")
    processor.centers.add(command_center)
    return processor


def _write_source_video(path: Path, content: bytes = b"kcache source video") -> Path:
    return atomic_write_file(
        destination=path,
        content=(content,),
        required_bytes=len(content),
    )


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault(DJANGO_SETTINGS_MODULE_ENV, "endoreg_db.config.settings.test")
    return env


def _run_json_subprocess(code: str, *args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=BASE_DIR,
        env=_subprocess_env(),
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, object], json.loads(result.stdout))


def test_kcache_video_import_dry_run_without_names_does_not_import_ingest(
    tmp_path: Path,
) -> None:
    source_path = _write_source_video(tmp_path / "dry-run-lazy.mp4")

    result = _run_json_subprocess(
        """
import json
import sys
from io import StringIO

import django
from django.core.management import call_command

django.setup()
from endoreg_db.utils.paths import EndoregPathsModel

stdout = StringIO()
call_command("kcache_video_import", sys.argv[1], "--json", stdout=stdout)
print(json.dumps({
    "payload": json.loads(stdout.getvalue()),
    "expected_watcher_video_drop": str(
        EndoregPathsModel.from_environment().watcher_video_drop
    ),
    "ingest_loaded": "endoreg_db.services.hub.ingest" in sys.modules,
}))
""",
        str(source_path),
    )

    payload = cast(dict[str, object], result["payload"])
    assert payload["status"] == "would_ingest"
    assert Path(cast(str, payload["watched_path"])).parent == Path(
        cast(str, result["expected_watcher_video_drop"])
    )
    assert result["ingest_loaded"] is False


def test_video_import_service_import_is_lazy_for_video_anonymizer() -> None:
    result = _run_json_subprocess(
        """
import importlib
import json
import sys

import django

django.setup()
heavy_modules = [
    "endoreg_db.import_files.processing.video_processing.video_anonymization",
    "lx_anonymizer.frame_cleaner",
]
module = importlib.import_module("endoreg_db.import_files.video_import_service")
print(json.dumps({
    "video_anonymizer_is_none": module.VideoAnonymizer is None,
    "loaded_heavy_modules": [
        module_name for module_name in heavy_modules if module_name in sys.modules
    ],
}))
"""
    )

    assert result["video_anonymizer_is_none"] is True
    assert result["loaded_heavy_modules"] == []


def test_kcache_video_import_dry_run_reports_watcher_target_without_ingest(
    tmp_path: Path,
    isolated_runtime_paths: EndoregPathsModel,
    command_center: Center,
    command_processor: EndoscopyProcessor,
) -> None:
    source_path = _write_source_video(tmp_path / "dry-run.mp4")

    stdout = StringIO()
    call_command(
        "kcache_video_import",
        str(source_path),
        "--center-name",
        command_center.name,
        "--processor-name",
        command_processor.name,
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    watched_path = Path(payload["watched_path"])
    assert payload["apply"] is False
    assert payload["status"] == "would_ingest"
    assert payload["center_name"] == command_center.name
    assert payload["processor_name"] == command_processor.name
    assert payload["source_sha256"] == sha256_file(source_path)
    assert watched_path.parent == isolated_runtime_paths.watcher_video_drop
    assert not watched_path.exists()
    assert UploadJob.objects.count() == 0


def test_kcache_video_import_dry_run_writes_profile_artifacts(
    tmp_path: Path,
    isolated_runtime_paths: EndoregPathsModel,
    command_center: Center,
    command_processor: EndoscopyProcessor,
) -> None:
    source_path = _write_source_video(tmp_path / "dry-run-profile.mp4")
    profile_path = tmp_path / "kcache.prof"
    summary_path = tmp_path / "kcache-profile.txt"

    stdout = StringIO()
    call_command(
        "kcache_video_import",
        str(source_path),
        "--center-name",
        command_center.name,
        "--processor-name",
        command_processor.name,
        "--profile-output",
        str(profile_path),
        "--profile-summary-output",
        str(summary_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "would_ingest"
    assert payload["profile_output"] == str(profile_path)
    assert payload["profile_summary_output"] == str(summary_path)
    assert profile_path.exists()
    assert profile_path.stat().st_size > 0
    assert "function calls" in summary_path.read_text(encoding="utf-8")


def test_kcache_video_import_dry_run_without_names_does_not_resolve_database_defaults(
    tmp_path: Path,
    isolated_runtime_paths: EndoregPathsModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = _write_source_video(tmp_path / "dry-run-no-db.mp4")

    def fail_resolve_center(center_name: str | None) -> Center:
        _ = center_name
        raise AssertionError("dry-run must not resolve a center without --center-name")

    def fail_resolve_processor(processor_name: str | None) -> EndoscopyProcessor:
        _ = processor_name
        raise AssertionError(
            "dry-run must not resolve a processor without --processor-name"
        )

    monkeypatch.setattr(command_module, "_resolve_center", fail_resolve_center)
    monkeypatch.setattr(command_module, "_resolve_processor", fail_resolve_processor)

    stdout = StringIO()
    call_command(
        "kcache_video_import",
        str(source_path),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    watched_path = Path(payload["watched_path"])
    assert payload["status"] == "would_ingest"
    assert payload["center_name"] is None
    assert payload["processor_name"] is None
    assert payload["django_settings_module"]
    assert payload["database_name"]
    assert watched_path.parent == isolated_runtime_paths.watcher_video_drop


def test_kcache_video_import_apply_invokes_concrete_video_import_service(
    tmp_path: Path,
    isolated_runtime_paths: EndoregPathsModel,
    command_center: Center,
    command_processor: EndoscopyProcessor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.import_files.video_import_service import (
        VideoImportService as ConcreteVideoImportService,
    )

    source_path = _write_source_video(tmp_path / "apply.mp4", b"kcache apply video")
    service_calls: list[Path] = []

    def fake_import_and_anonymize(
        self: ConcreteVideoImportService,
        *,
        file_path: Path,
        center_name: str,
        processor_name: str,
        retry: bool,
    ) -> VideoFile:
        assert ConcreteVideoImportService in type(self).mro()
        assert center_name == command_center.name
        assert processor_name == command_processor.name
        assert retry is False
        assert file_path.exists()
        service_calls.append(file_path)

        video = VideoFile.objects.create(
            center=command_center,
            processor=command_processor,
            original_file_name=file_path.name,
            video_hash=sha256_file(file_path),
            suffix=file_path.suffix,
        )
        state = video.get_or_create_state()
        state.mark_processing_started()
        state.mark_frames_extracted()
        state.mark_anonymized()
        state.mark_sensitive_meta_processed()
        state.mark_anonymization_validated()
        return video

    monkeypatch.setattr(
        ConcreteVideoImportService,
        "import_and_anonymize",
        fake_import_and_anonymize,
    )

    stdout = StringIO()
    call_command(
        "kcache_video_import",
        str(source_path),
        "--center-name",
        command_center.name,
        "--processor-name",
        command_processor.name,
        "--drop-name",
        "profile-run.mp4",
        "--apply",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    watched_path = Path(payload["watched_path"])
    assert payload["apply"] is True
    assert payload["status"] == UploadJob.Status.ANONYMIZED
    assert payload["upload_job_created"] is True
    assert payload["inline_ingest_ran"] is True
    assert payload["watched_path_exists"] is False
    assert watched_path == isolated_runtime_paths.watcher_video_drop / "profile-run.mp4"
    assert service_calls == [watched_path]

    upload_job = UploadJob.objects.get(id=payload["upload_job_id"])
    assert upload_job.ingest_mode == UploadJob.IngestMode.WATCHER
    assert upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_WATCHER
    assert upload_job.retention_policy == UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert upload_job.processing_provenance["ingest_variant"] == "kcache_video_import"
    assert upload_job.processing_provenance["file_type"] == "video"
    assert upload_job.processing_provenance["processor_name"] == command_processor.name

    video_payload = payload["video"]
    assert video_payload["video_hash"] == upload_job.content_hash
    assert video_payload["anonymization_status"] == "validated"
    assert video_payload["anonymized"] is True
    assert video_payload["sensitive_meta_processed"] is True


def test_kcache_video_import_apply_reruns_incomplete_command_owned_upload_job(
    tmp_path: Path,
    isolated_runtime_paths: EndoregPathsModel,
    command_center: Center,
    command_processor: EndoscopyProcessor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.import_files.video_import_service import (
        VideoImportService as ConcreteVideoImportService,
    )
    from endoreg_db.services.hub import ingest as ingest_module

    source_content = b"kcache retry source video"
    source_path = _write_source_video(tmp_path / "retry.mp4", source_content)
    previous_watched_path = _write_source_video(
        isolated_runtime_paths.watcher_video_drop / "previous-retry.mp4",
        source_content,
    )
    existing_upload_job, created = ingest_module.create_or_reuse_watcher_upload_job(
        file_path=previous_watched_path,
        content_type="video/mp4",
        source_center=command_center,
        source_system="watcher",
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance={
            "file_type": "video",
            "processor_name": command_processor.name,
            "ingest_variant": "kcache_video_import",
        },
    )
    assert created is True
    existing_upload_job.status = UploadJob.Status.PROCESSING
    existing_upload_job.save(update_fields=["status", "updated_at"])
    safe_unlink_file(previous_watched_path, missing_ok=True)

    service_calls: list[Path] = []

    def fake_import_and_anonymize(
        self: ConcreteVideoImportService,
        *,
        file_path: Path,
        center_name: str,
        processor_name: str,
        retry: bool,
    ) -> VideoFile:
        assert ConcreteVideoImportService in type(self).mro()
        assert center_name == command_center.name
        assert processor_name == command_processor.name
        assert retry is False
        assert file_path.exists()
        service_calls.append(file_path)

        video = VideoFile.objects.create(
            center=command_center,
            processor=command_processor,
            original_file_name=file_path.name,
            video_hash=sha256_file(file_path),
            suffix=file_path.suffix,
        )
        state = video.get_or_create_state()
        state.mark_processing_started()
        state.mark_frames_extracted()
        state.mark_anonymized()
        state.mark_sensitive_meta_processed()
        state.mark_anonymization_validated()
        return video

    monkeypatch.setattr(
        ConcreteVideoImportService,
        "import_and_anonymize",
        fake_import_and_anonymize,
    )

    stdout = StringIO()
    call_command(
        "kcache_video_import",
        str(source_path),
        "--center-name",
        command_center.name,
        "--processor-name",
        command_processor.name,
        "--drop-name",
        "retry-profile-run.mp4",
        "--apply",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    watched_path = Path(payload["watched_path"])
    assert payload["upload_job_id"] == str(cast(object, existing_upload_job.id))
    assert payload["upload_job_created"] is False
    assert payload["upload_job_reused_for_inline"] is True
    assert payload["inline_ingest_ran"] is True
    assert payload["status"] == UploadJob.Status.ANONYMIZED
    assert payload["watched_path_exists"] is False
    assert watched_path == isolated_runtime_paths.watcher_video_drop / (
        "retry-profile-run.mp4"
    )
    assert service_calls == [watched_path]

    existing_upload_job.refresh_from_db()
    assert existing_upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert existing_upload_job.processing_provenance["ingest_variant"] == (
        "kcache_video_import"
    )


def test_kcache_video_import_payload_backfills_missing_processed_video_hash(
    tmp_path: Path,
    command_center: Center,
    command_processor: EndoscopyProcessor,
) -> None:
    from endoreg_db.services.hub import ingest as ingest_module

    source_content = b"kcache processed hash source"
    processed_content = b"kcache processed hash artifact"
    watched_path = _write_source_video(
        tmp_path / "backfill-processed-hash.mp4",
        source_content,
    )
    upload_job, created = ingest_module.create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type="video/mp4",
        source_center=command_center,
        source_system="watcher",
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance={
            "file_type": "video",
            "processor_name": command_processor.name,
            "ingest_variant": "kcache_video_import",
        },
    )
    assert created is True

    video = VideoFile.objects.create(
        center=command_center,
        processor=command_processor,
        original_file_name=watched_path.name,
        video_hash=upload_job.content_hash,
        suffix=watched_path.suffix,
    )
    video.processed_file.save(
        "processed_videos_final/backfill-processed-hash.mp4",
        ContentFile(processed_content),
        save=True,
    )
    video.processed_video_hash = None
    video.save(update_fields=["processed_video_hash", "date_modified"])

    payload = command_module.video_payload_for_upload_job(upload_job)

    expected_hash = sha256(processed_content).hexdigest()
    video.refresh_from_db()
    assert payload is not None
    assert payload["processed_video_hash"] == expected_hash
    assert video.processed_video_hash == expected_hash


def test_test_settings_management_commands_reuse_stable_test_database() -> None:
    env = os.environ.copy()
    env[DJANGO_SETTINGS_MODULE_ENV] = "endoreg_db.config.settings.test"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("TEST_DB_REUSE", None)
    env.pop("TEST_DB_FILE", None)
    env.pop("TEST_DB_NAME", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from django.conf import settings; "
                "print(settings.DATABASES['default']['NAME'])"
            ),
        ],
        cwd=BASE_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == str(
        BASE_DIR / "endoreg_db/data/tests/db/test_db.sqlite3"
    )
