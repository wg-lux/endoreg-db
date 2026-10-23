from __future__ import annotations

import os
from hashlib import sha256
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.fields.files import FieldFile

from endoreg_db.management.commands import migrate_media_storage as command_module
from endoreg_db.models import Center, RawPdfFile, VideoFile
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    to_protected_media_relative,
)
from endoreg_db.utils.storage import save_local_file
from lx_dtypes.models.contracts.migrate_media_storage import (
    MigrateMediaStorageSummaryPayload,
)

pytestmark = pytest.mark.django_db


def test_reconciliation_accepts_legacy_absolute_pdf_reference(
    media_center: Center,
) -> None:
    paths = EndoregPathsModel.from_environment()
    payload = b"legacy absolute report"
    identity = sha256(payload).hexdigest()
    source = paths.import_report / "absolute-reference.PDF"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    report = RawPdfFile.objects.create(center=media_center, pdf_hash=identity)
    # Reproduce a persisted legacy reference without invoking today's write policy.
    RawPdfFile.objects.filter(pk=report.pk).update(file=str(source))

    summary = _json_command("--apply", "--include-reports", "--hash", identity)

    report.refresh_from_db()
    assert report.file.name == f"sensitive_reports/{identity}.pdf"
    assert summary.migrated == 1
    assert source.read_bytes() == payload


@pytest.mark.parametrize("kind", ["video", "report"])
def test_reconciliation_finds_lost_original_filename_by_content_hash(
    media_center: Center, kind: str
) -> None:
    paths = EndoregPathsModel.from_environment()
    payload = f"lost original filename for {kind}".encode()
    identity = sha256(payload).hexdigest()
    if kind == "video":
        instance = VideoFile.objects.create(
            center=media_center, raw_video_hash=identity
        )
        source = paths.import_video / "original-camera-name.mp4"
        flags = ("--include-raw", "--video-id", str(instance.pk))
    else:
        instance = RawPdfFile.objects.create(center=media_center, pdf_hash=identity)
        source = paths.import_report / "original-scanner-name.pdf"
        flags = ("--include-reports", "--hash", identity)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)

    summary = _json_command("--apply", *flags)

    instance.refresh_from_db()
    field = instance.raw_file if isinstance(instance, VideoFile) else instance.file
    assert field.name is not None
    assert Path(field.name).stem == identity
    assert summary.migrated == 1
    assert source.read_bytes() == payload


@pytest.mark.parametrize("kind", ["video", "report"])
def test_reconcile_legacy_filename_into_canonical_storage(
    media_center: Center, kind: str
) -> None:
    paths = EndoregPathsModel.from_environment()
    identity = f"legacy-filename-{kind}"
    if kind == "video":
        instance = VideoFile.objects.create(
            center=media_center, raw_video_hash=identity
        )
        field = instance.raw_file
        directory = paths.sensitive_video
        suffix = ".mp4"
        flags = ("--include-raw", "--video-id", str(instance.pk))
    else:
        instance = RawPdfFile.objects.create(center=media_center, pdf_hash=identity)
        field = instance.file
        directory = paths.sensitive_report
        suffix = ".pdf"
        flags = ("--include-reports", "--hash", identity)
    source = _write_plaintext_field_file(
        field, f"{directory.name}/old-{identity}{suffix}", b"legacy artifact"
    )

    result = _json_command("--apply", *flags)
    instance.refresh_from_db()
    current = instance.raw_file if isinstance(instance, VideoFile) else instance.file
    assert current.name == f"{directory.name}/{identity}{suffix}"
    assert result.failed == 0
    assert source.read_bytes() == b"legacy artifact"
    assert _starts_with_magic(Path(current.path))
    assert _json_command("--apply", *flags).migrated == 0


def test_reconciliation_rejects_competing_legacy_pdf_candidates(
    media_center: Center,
) -> None:
    paths = EndoregPathsModel.from_environment()
    identity = "ambiguous-legacy-report"
    RawPdfFile.objects.create(
        center=media_center, pdf_hash=identity, processed_file="missing.pdf"
    )
    first = paths.anonym_report / f"{identity}_first.pdf"
    second = paths.import_anonymized_report / f"{identity}_second.pdf"
    for path, payload in ((first, b"first"), (second, b"second")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    summary = _json_command("--include-reports", "--hash", identity)
    assert summary.failed == 1
    assert any(record.reason == "ambiguous_source" for record in summary.records)
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


@pytest.mark.parametrize("same_content", [True, False])
def test_reconciliation_preserves_occupied_canonical_destination(
    media_center: Center, tmp_path: Path, same_content: bool
) -> None:
    paths = EndoregPathsModel.from_environment()
    identity = f"occupied-canonical-{same_content}"
    report = RawPdfFile.objects.create(center=media_center, pdf_hash=identity)
    source = _write_plaintext_field_file(
        report.file, f"sensitive_reports/legacy-{identity}.pdf", b"original"
    )
    destination_name = f"{paths.sensitive_report.name}/{identity}.pdf"
    payload = b"original" if same_content else b"other content"
    staged = tmp_path / "candidate.pdf"
    staged.write_bytes(payload)
    target_field = FieldFile(report, report.file.field, "")
    save_local_file(target_field, staged, name=destination_name, save=False)
    destination = Path(target_field.path)
    ciphertext_before = destination.read_bytes()

    summary = _json_command("--apply", "--include-reports", "--hash", identity)

    report.refresh_from_db()
    assert destination.read_bytes() == ciphertext_before
    assert source.read_bytes() == b"original"
    if same_content:
        assert report.file.name == destination_name
        assert summary.failed == 0
    else:
        assert report.file.name == f"sensitive_reports/legacy-{identity}.pdf"
        assert summary.failed == 1


@pytest.fixture(autouse=True)
def enable_destructive_migration_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        command_module,
        "video_storage_destructive_migration_enabled",
        lambda: True,
    )


def _json_command(*args: str) -> MigrateMediaStorageSummaryPayload:
    output = StringIO()
    call_command("migrate_media_storage", *args, "--json", stdout=output)
    return MigrateMediaStorageSummaryPayload.model_validate_json(output.getvalue())


@pytest.fixture
def media_center() -> Center:
    return Center.objects.create(
        name="media-storage-migration-center",
        display_name="Media Storage Migration Center",
    )


def _create_video(center: Center, raw_video_hash: str) -> VideoFile:
    return VideoFile.objects.create(center=center, raw_video_hash=raw_video_hash)


def _write_plaintext_field_file(
    field_file: FieldFile, name: str, payload: bytes
) -> Path:
    field_file.name = name
    field_file.instance.save(update_fields=[field_file.field.name])
    path = Path(field_file.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _starts_with_magic(path: Path) -> bool:
    return path.read_bytes().startswith(MAGIC)


def test_migrate_media_storage_apply_requires_release_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        command_module,
        "video_storage_destructive_migration_enabled",
        lambda: False,
    )

    assert _json_command("--include-raw").dry_run is True
    with pytest.raises(CommandError, match="clinical_frame_quality"):
        _json_command("--apply", "--include-raw")


def test_migrate_media_storage_dry_run_changes_nothing(media_center: Center) -> None:
    video = _create_video(media_center, "dry-run-video")
    stored_path = _write_plaintext_field_file(
        video.raw_file,
        "sensitive_videos/dry-run-video.mp4",
        b"\x00\x00\x00\x18ftypmp42dry-run",
    )
    before_mtime = stored_path.stat().st_mtime_ns

    summary = _json_command("--include-raw")

    assert summary.dry_run is True
    assert summary.would_repair == 1
    assert summary.changed == 0
    assert stored_path.stat().st_mtime_ns == before_mtime
    assert not _starts_with_magic(stored_path)


def test_migrate_media_storage_discovers_legacy_processed_stem_variant(
    media_center: Center,
) -> None:
    video = _create_video(media_center, "processed-stem-video")
    source = (
        EndoregPathsModel.from_environment().anonym_video
        / "processed-stem-video_processed.mp4"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42processed-stem")

    try:
        summary = _json_command(
            "--include-processed",
            "--video-id",
            str(video.pk),
        )

        assert summary.would_migrate == 1
        assert summary.records[0].source_path == str(source.resolve())
    finally:
        source.unlink(missing_ok=True)


def test_migrate_media_storage_second_run_is_noop(media_center: Center) -> None:
    video = _create_video(media_center, "idempotent-video")
    stored_path = _write_plaintext_field_file(
        video.raw_file,
        "sensitive_videos/idempotent-video.mp4",
        b"\x00\x00\x00\x18ftypmp42idempotent",
    )

    first = _json_command("--apply", "--include-raw")
    second = _json_command("--apply", "--include-raw")

    assert first.repaired == 1
    assert first.changed == 1
    assert second.changed == 0
    assert second.selected == 0
    assert _starts_with_magic(stored_path)
    video.refresh_from_db()
    with video.raw_file.open("rb") as stored:
        assert stored.read() == b"\x00\x00\x00\x18ftypmp42idempotent"


def test_migrate_media_storage_limit_allows_resume(media_center: Center) -> None:
    first_video = _create_video(media_center, "resume-video-1")
    second_video = _create_video(media_center, "resume-video-2")
    first_path = _write_plaintext_field_file(
        first_video.raw_file,
        "sensitive_videos/resume-video-1.mp4",
        b"\x00\x00\x00\x18ftypmp42resume-1",
    )
    second_path = _write_plaintext_field_file(
        second_video.raw_file,
        "sensitive_videos/resume-video-2.mp4",
        b"\x00\x00\x00\x18ftypmp42resume-2",
    )

    first = _json_command("--apply", "--include-raw", "--limit", "1")
    second = _json_command("--apply", "--include-raw", "--limit", "1")
    third = _json_command("--apply", "--include-raw", "--limit", "1")

    assert first.changed == 1
    assert second.changed == 1
    assert third.changed == 0
    assert _starts_with_magic(first_path)
    assert _starts_with_magic(second_path)


def test_migrate_media_storage_missing_source_is_reported(
    media_center: Center,
) -> None:
    video = _create_video(media_center, "missing-source-video")
    video.raw_file.name = "sensitive_videos/missing-source-video.mp4"
    video.save(update_fields=["raw_file"])

    summary = _json_command("--apply", "--include-raw")

    assert summary.failed == 1
    assert summary.records[0].reason == "missing_source"
    assert summary.changed == 0


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        ("legacy_path", "validation_failed"),
        ("streamable_path", "encrypted_blob_in_streamable_path"),
    ],
)
def test_plaintext_candidate_inspection_rejects_invalid_content(
    tmp_path: Path,
    kind: command_module.SourceKind,
    expected_status: command_module.CandidateContentStatus,
) -> None:
    source = tmp_path / f"encrypted-{kind}.mp4"
    source.write_bytes(MAGIC + b"encrypted")
    candidate = command_module.SourceCandidate(source, kind, "test")

    status = command_module._inspect_candidate_content(  # pyright: ignore[reportPrivateUsage]
        candidate,
        is_allowed_source_path=lambda _path: True,
    )

    assert status == expected_status


def test_plaintext_candidate_inspection_rejects_empty_file(tmp_path: Path) -> None:
    source = tmp_path / "empty.mp4"
    source.touch()

    status = command_module._inspect_candidate_file(  # pyright: ignore[reportPrivateUsage]
        source
    )

    assert status == "validation_failed"


def test_migrate_media_storage_deletes_legacy_source_only_with_explicit_flag(
    media_center: Center,
) -> None:
    paths = EndoregPathsModel.from_environment()
    source = paths.import_video / "delete-legacy-video.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42delete-legacy")
    video = _create_video(media_center, "delete-legacy-video")

    dry_summary = _json_command("--apply", "--include-raw")
    video.refresh_from_db()

    assert dry_summary.migrated == 1
    assert source.exists()
    with video.raw_file.open("rb") as stored:
        assert stored.read() == b"\x00\x00\x00\x18ftypmp42delete-legacy"

    second_video = _create_video(media_center, "delete-legacy-video-2")
    second_source = paths.import_video / "delete-legacy-video-2.mp4"
    second_source.write_bytes(b"\x00\x00\x00\x18ftypmp42delete-legacy-2")

    delete_summary = _json_command(
        "--apply",
        "--include-raw",
        "--delete-verified-legacy",
        "--video-id",
        str(second_video.pk),
    )

    assert delete_summary.migrated == 1
    assert delete_summary.cleanup_deleted == 1
    assert not second_source.exists()


def test_migrate_media_storage_keeps_legacy_when_verify_breaks(
    media_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = EndoregPathsModel.from_environment()
    source = paths.import_video / "validation-fails-video.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"\x00\x00\x00\x18ftypmp42validation-fails")
    video = _create_video(media_center, "validation-fails-video")

    def unreadable_after_save(field_file: FieldFile) -> bool:
        return False

    monkeypatch.setattr(
        command_module,
        "field_file_is_readable",
        unreadable_after_save,
        raising=True,
    )

    summary = _json_command(
        "--apply",
        "--include-raw",
        "--delete-verified-legacy",
        "--video-id",
        str(video.pk),
    )

    assert summary.failed == 1
    assert summary.records[0].reason == "validation_failed"
    assert source.exists()


def test_migrate_media_storage_removes_bad_streamable_object(
    media_center: Center,
    tmp_path: Path,
) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    raw_video_hash = f"streamable-video-{worker}"
    video = _create_video(media_center, raw_video_hash)
    source = tmp_path / f"{raw_video_hash}.mp4"
    source.write_bytes(Path("tests/assets/test.mp4").read_bytes())
    save_local_file(
        video.processed_file,
        source,
        name=f"{raw_video_hash}.mp4",
        save=False,
    )
    video.save(update_fields=["processed_file"])

    paths = EndoregPathsModel.from_environment()
    streamable_path = (
        paths.storage
        / "streamable_videos"
        / "processed"
        / (f"{video.raw_video_hash}.mp4")
    )
    streamable_path.parent.mkdir(parents=True, exist_ok=True)
    streamable_path.write_bytes(MAGIC + b"bad-streamable")
    video.processed_streamable_relative_path = to_protected_media_relative(
        streamable_path
    )
    video.save(update_fields=["processed_streamable_relative_path"])

    summary = _json_command(
        "--apply",
        "--include-processed",
        "--include-streamable",
        "--video-id",
        str(video.pk),
    )

    assert summary.failed == 0, summary.model_dump_json()
    assert summary.changed == 1
    assert not streamable_path.exists()
    video.refresh_from_db()
    assert video.processed_streamable_relative_path == ""


def test_migrate_media_storage_migrates_report_fields(media_center: Center) -> None:
    paths = EndoregPathsModel.from_environment()
    source = paths.import_report / "report-migration.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.4\nreport-migration\n%%EOF\n")
    report = RawPdfFile.objects.create(
        center=media_center,
        pdf_hash="report-migration",
    )

    summary = _json_command("--apply", "--include-reports")

    assert summary.migrated == 1
    report.refresh_from_db()
    assert report.file.name
    assert Path(report.file.path).read_bytes().startswith(MAGIC)
    with report.file.open("rb") as stored:
        assert stored.read() == b"%PDF-1.4\nreport-migration\n%%EOF\n"
