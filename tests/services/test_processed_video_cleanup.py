from endoreg_db.utils.paths import get_runtime_paths

# pyright: reportPrivateUsage=false
from dataclasses import dataclass
from collections.abc import Callable
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone
from pydantic import ValidationError

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import (
    ProcessedGenerationCleanupReceipt,
    cleanup_receipts,
)
from endoreg_db.services import hls_media, processed_video_cleanup as cleanup
from endoreg_db.utils.paths import (
    EndoregPathsModel,
    to_protected_media_relative,
    to_storage_relative,
)
from tests.import_files.test_video_finalize_success import _normalization_evidence

pytestmark = pytest.mark.django_db


@dataclass
class Replacement:
    video: VideoFile
    old_name: str
    old_hls: Path
    old_artifact: VideoHlsArtifact
    ready: VideoHlsArtifact


@pytest.fixture
def replacement(monkeypatch: pytest.MonkeyPatch) -> Replacement:
    center = Center.objects.create(name=f"cleanup-{uuid4().hex}")
    video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
    root = EndoregPathsModel.from_environment().anonym_video
    old_name = video.processed_file.storage.save(
        to_storage_relative(root / f"{video.raw_video_hash}.mp4"),
        ContentFile(b"old processed"),
    )
    new_name = video.processed_file.storage.save(
        to_storage_relative(
            root / ".generations" / f"{video.raw_video_hash}-{uuid4().hex}.mp4"
        ),
        ContentFile(b"new processed"),
    )
    video.processed_file.name = new_name
    video.processed_video_hash = sha256(b"new processed").hexdigest()
    video.meta = {
        "storage_normalization": _normalization_evidence().model_dump(mode="json")
    }
    cleanup.record_processed_replacement(
        video,
        previous_name=old_name,
        previous_hash=sha256(b"old processed").hexdigest(),
    )
    cleanup.commit_processed_replacements(video)
    video.save()
    key = uuid4()
    directory = (
        get_runtime_paths().streamable_videos_processed_media
        / "hls"
        / str(video.uuid)
        / str(key)
        / "v0"
    )
    directory.mkdir(parents=True)
    (directory / "playlist.m3u8").write_text("#EXTM3U\nseg_00000.ts\n")
    (directory / "seg_00000.ts").write_bytes(b"old encrypted segment")
    old = VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status="superseded",
        key_id=key,
        source_file_name=old_name,
        source_content_hash=sha256(b"old processed").hexdigest(),
        playlist_relative_path=to_protected_media_relative(directory / "playlist.m3u8"),
        segment_directory_relative_path=to_protected_media_relative(directory),
        segment_count=1,
    )
    ready = VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status="ready",
        source_file_name=new_name,
        source_content_hash=video.processed_video_hash,
    )

    def ready_artifact(**kwargs: object) -> VideoHlsArtifact:
        return ready

    # HLS validation has its own integration suite; exercise the cleanup boundary
    # against a validated generation while using real storage and database rows.
    monkeypatch.setattr(hls_media, "get_ready_hls_artifact", ready_artifact)
    return Replacement(video, old_name, directory, old, ready)


def test_dry_run_then_apply_is_idempotent(replacement: Replacement) -> None:
    video = replacement.video
    assert cleanup.cleanup_processed_video_generations(video.pk).reason == "dry_run"
    assert video.processed_file.storage.exists(replacement.old_name)
    assert replacement.old_hls.exists()
    result = cleanup.cleanup_processed_video_generations(video.pk, apply=True)
    assert result.cleaned == 1
    assert not video.processed_file.storage.exists(replacement.old_name)
    assert not replacement.old_hls.exists()
    assert not VideoHlsArtifact.objects.filter(pk=replacement.old_artifact.pk).exists()
    assert video.processed_file.storage.exists(str(video.processed_file.name))
    assert VideoHlsArtifact.objects.filter(pk=replacement.ready.pk).exists()
    assert (
        cleanup.cleanup_processed_video_generations(video.pk, apply=True).reason
        == "nothing_pending"
    )


@pytest.mark.parametrize(
    "blocker",
    [
        "lease",
        "uncommitted",
        "raw_reference",
        "other_video",
        "hls_reference",
        "wrong_replacement",
    ],
)
def test_blockers_preserve_previous_generation(
    replacement: Replacement, blocker: str
) -> None:
    video = replacement.video
    if blocker == "lease":
        MediaOperationLease.objects.create(
            video=video,
            lease_type="stream",
            expires_at=timezone.now() + timedelta(minutes=5),
        )
    elif blocker == "uncommitted":
        receipts = cleanup_receipts(video.meta)
        assert video.meta is not None
        video.meta["processed_generation_cleanup"] = [
            receipts[0].model_copy(update={"committed": False}).model_dump(mode="json")
        ]
        video.save()
    elif blocker == "raw_reference":
        video.raw_file.name = replacement.old_name
        video.save()
    elif blocker == "other_video":
        VideoFile.objects.create(
            center=video.center,
            raw_video_hash=uuid4().hex,
            processed_file=replacement.old_name,
        )
    elif blocker == "hls_reference":
        replacement.old_artifact.status = "failed"
        replacement.old_artifact.error_code = (
            VideoHlsArtifact.ErrorCode.MATERIALIZATION_FAILED
        )
        replacement.old_artifact.save()
    else:
        video.processed_video_hash = "0" * 64
        video.save()
    with (
        patch.object(cleanup, "get_file_hash", wraps=cleanup.get_file_hash) as digest,
        patch.object(
            hls_media, "get_ready_hls_artifact", wraps=hls_media.get_ready_hls_artifact
        ) as ready,
    ):
        result = cleanup.cleanup_processed_video_generations(video.pk, apply=True)
    if blocker in {"raw_reference", "other_video"}:
        assert result.reason == "referenced"
        digest.assert_not_called()
        ready.assert_not_called()
    assert result.cleaned == 0
    assert result.pending == 1
    assert replacement.old_hls.exists()
    assert video.processed_file.storage.exists(replacement.old_name)


def test_reference_added_during_validation_preserves_old(
    replacement: Replacement,
) -> None:
    video = replacement.video

    def ready_with_new_reference(**kwargs: object) -> VideoHlsArtifact:
        VideoFile.objects.create(
            center=video.center,
            raw_video_hash=uuid4().hex,
            processed_file=replacement.old_name,
        )
        return replacement.ready

    with patch.object(hls_media, "get_ready_hls_artifact", ready_with_new_reference):
        result = cleanup.cleanup_processed_video_generations(video.pk, apply=True)
    assert result.reason == "referenced"
    assert result.cleaned == 0 and result.pending == 1
    assert video.processed_file.storage.exists(replacement.old_name)
    assert replacement.old_hls.exists()


def test_missing_replacement_preserves_old(replacement: Replacement) -> None:
    replacement.video.processed_file.storage.delete(
        str(replacement.video.processed_file.name)
    )
    assert (
        cleanup.cleanup_processed_video_generations(
            replacement.video.pk, apply=True
        ).reason
        == "replacement_not_ready"
    )
    assert replacement.old_hls.exists()


def test_digest_mismatch_deletes_nothing(replacement: Replacement) -> None:
    with patch.object(
        cleanup,
        "get_file_hash",
        side_effect=[replacement.video.processed_video_hash, "0" * 64],
    ):
        with pytest.raises(ValueError, match="digest differs"):
            cleanup.cleanup_processed_video_generations(
                replacement.video.pk, apply=True
            )
    assert replacement.old_hls.exists()


def test_interrupted_deletion_keeps_receipt_and_retries(
    replacement: Replacement,
) -> None:
    with patch.object(
        cleanup, "safe_delete_field_file", side_effect=OSError("storage unavailable")
    ):
        with pytest.raises(OSError):
            cleanup.cleanup_processed_video_generations(
                replacement.video.pk, apply=True
            )
    replacement.video.refresh_from_db()
    assert len(cleanup_receipts(replacement.video.meta)) == 1
    assert not replacement.old_hls.exists()
    assert VideoHlsArtifact.objects.filter(pk=replacement.old_artifact.pk).exists()
    assert (
        cleanup.cleanup_processed_video_generations(
            replacement.video.pk, apply=True
        ).cleaned
        == 1
    )


def test_symlink_blocks_all_deletion(replacement: Replacement, tmp_path: Path) -> None:
    outside = tmp_path / "external"
    outside.write_bytes(b"retained")
    (replacement.old_hls / "alias.ts").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        cleanup.cleanup_processed_video_generations(replacement.video.pk, apply=True)
    assert outside.read_bytes() == b"retained"
    assert replacement.video.processed_file.storage.exists(replacement.old_name)


def test_command_defaults_to_dry_run(replacement: Replacement) -> None:
    call_command("reap_processed_video_generations", video_id=replacement.video.pk)
    assert replacement.old_hls.exists()
    call_command(
        "reap_processed_video_generations", video_id=replacement.video.pk, apply=True
    )
    assert not replacement.old_hls.exists()


def test_hls_retirement_defers_to_master_cleanup(replacement: Replacement) -> None:
    replacement.old_artifact.status = "ready"
    snapshot = hls_media._artifact_snapshot(replacement.old_artifact)
    replacement.old_artifact.status = "superseded"
    assert snapshot is not None
    hls_media._cleanup_replaced_artifact(snapshot)
    assert replacement.old_hls.exists()
    assert VideoHlsArtifact.objects.filter(pk=replacement.old_artifact.pk).exists()


def test_cleanup_is_scheduled_only_after_commit(replacement: Replacement) -> None:
    from django.db import transaction

    callbacks: list[object] = []
    with patch.object(transaction, "on_commit", side_effect=callbacks.append):
        cleanup.schedule_processed_generation_cleanup(replacement.video.pk)
    assert len(callbacks) == 1
    assert replacement.old_hls.exists()
    callback = callbacks[0]
    assert callable(callback)
    callback()
    assert not replacement.old_hls.exists()


def test_callback_failure_does_not_fail_committed_import(
    replacement: Replacement,
) -> None:
    from django.db import transaction

    def invoke(callback: Callable[[], None]) -> None:
        callback()

    with patch.object(transaction, "on_commit", side_effect=invoke):
        with patch.object(
            cleanup,
            "safe_delete_field_file",
            side_effect=OSError("storage unavailable"),
        ):
            cleanup.schedule_processed_generation_cleanup(replacement.video.pk)
    replacement.video.refresh_from_db()
    assert cleanup_receipts(replacement.video.meta)
    assert replacement.video.processed_file.storage.exists(
        str(replacement.video.processed_file.name)
    )


@pytest.mark.parametrize(
    "name", ["../master.mp4", "/master.mp4", "a//b.mp4", "a/./b.mp4", "a\\b.mp4"]
)
def test_receipt_rejects_path_aliases(name: str) -> None:
    with pytest.raises(ValidationError):
        ProcessedGenerationCleanupReceipt(
            source_name=name,
            source_sha256="a" * 64,
            replacement_name="processed/new.mp4",
            replacement_sha256="b" * 64,
        )


def test_finalization_records_cleanup_before_hls_and_schedules_after_success(
    replacement: Replacement,
    tmp_path: Path,
) -> None:
    from endoreg_db.import_files.context.import_context import ImportContext
    from endoreg_db.import_files.file_storage import state_management
    from django.db.models.fields.files import FieldFile

    source = tmp_path / "replacement.mp4"
    source.write_bytes(b"next processed")
    ctx = ImportContext(
        file_path=source,
        center_name=str(replacement.video.center.name),
        processor_name="olympus_cv_1500",
    )
    ctx.current_video = replacement.video
    ctx.file_hash = str(replacement.video.raw_video_hash)
    ctx.anonymized_path = source
    ctx.storage_normalization_evidence = _normalization_evidence()
    scheduled: list[int] = []
    previous_master = str(replacement.video.processed_file.name)

    def store(field: FieldFile, path: Path, *, relative_name: str) -> str:
        saved = field.storage.save(relative_name, ContentFile(path.read_bytes()))
        field.name = saved
        return saved

    def ensure_hls(video: VideoFile, **kwargs: object) -> None:
        receipts = cleanup_receipts(video.meta)
        assert len(receipts) == 1
        assert receipts[-1].source_name == previous_master
        assert receipts[-1].committed is False
        assert scheduled == []
        assert not replacement.old_hls.exists()
        assert video.processed_file.storage.exists(previous_master)

    with (
        patch.object(state_management, "_verify_final_video_output"),
        patch.object(state_management, "_store_existing_final_file", side_effect=store),
        patch.object(state_management, "ensure_video_hls", side_effect=ensure_hls),
        patch.object(state_management, "_record_successful_video_processing_history"),
        patch.object(state_management, "cleanup_staging_after_commit"),
        patch.object(
            state_management,
            "schedule_processed_generation_cleanup",
            side_effect=scheduled.append,
        ),
    ):
        state_management.finalize_video_success(ctx)
    replacement.video.refresh_from_db()
    assert all(item.committed for item in cleanup_receipts(replacement.video.meta))
    assert scheduled == [replacement.video.pk]
    assert not replacement.old_hls.exists()
    assert replacement.video.processed_file.storage.exists(previous_master)


@pytest.mark.django_db(transaction=True)
def test_cleanup_writer_is_visible_before_storage_transaction(
    replacement: Replacement,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections, connection
    from endoreg_db.services.media_operation_gate import video_transcode_lease

    real_cleanup = cleanup._cleanup_processed_video_generations_owned
    visible: list[bool] = []

    def observe_committed_writer() -> bool:
        close_old_connections()
        try:
            return MediaOperationLease.objects.filter(
                video_id=replacement.video.pk,
                lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE,
            ).exists()
        finally:
            close_old_connections()

    def inspect_before_transaction(
        video_id: int, *, apply: bool
    ) -> cleanup.ProcessedGenerationCleanupResult:
        assert not connection.in_atomic_block
        with ThreadPoolExecutor(max_workers=1) as executor:
            visible.append(executor.submit(observe_committed_writer).result(timeout=5))
        return real_cleanup(video_id, apply=apply)

    with video_transcode_lease(video_id=replacement.video.pk):
        with patch.object(
            cleanup,
            "_cleanup_processed_video_generations_owned",
            side_effect=inspect_before_transaction,
        ):
            result = cleanup.cleanup_processed_video_generations(
                replacement.video.pk, apply=True
            )
    assert visible == [True]
    assert result.cleaned == 1
    assert not replacement.video.processed_file.storage.exists(replacement.old_name)
