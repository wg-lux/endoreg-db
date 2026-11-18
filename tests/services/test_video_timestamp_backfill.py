from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile
from django.db import OperationalError, close_old_connections, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from endoreg_db.models import Center, Frame, LabelVideoSegment, VideoFile
from endoreg_db.models.hub.storage_placement import StorageRotationCleanupReceipt
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import VideoArtifactProbe
from endoreg_db.services.media import operation_gate
from endoreg_db.services.video_files import timestamp_backfill as service
from endoreg_db.services.video_files.frame_coordinates import artifact_frame_timestamps
from endoreg_db.services.video_files.timeline_index import load_video_timeline_index
from endoreg_db.services.video_files.metadata import (
    get_video_frame_neighborhood,
    video_seconds_to_frame_number,
)
from endoreg_db.services.media.export_downloads import timestamp_csv
from endoreg_db.utils import ffmpeg_wrapper, transcode_execution
from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.hashs import get_file_hash
from tests.helpers.video_timeline import constant_rate_video_metadata

pytestmark = pytest.mark.django_db


@pytest.fixture
def video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VideoFile:
    executable = shutil.which("ffmpeg")
    assert executable is not None
    monkeypatch.setattr(
        ffmpeg_wrapper, "get_stream_info", transcode_execution.get_stream_info
    )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="timestamp-backfill"), raw_video_hash="a" * 64
    )
    for kind, origin in (("raw", 2000), ("processed", 4000)):
        path = tmp_path / f"{kind}.mp4"
        subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x64:rate=10:duration=0.4",
                "-vf",
                f"settb=1/1000,setpts={origin}+100*N+50*N*N",
                "-fps_mode",
                "passthrough",
                "-enc_time_base",
                "1/1000",
                "-c:v",
                "libx264",
                "-bf",
                "0",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        field = video.raw_file if kind == "raw" else video.processed_file
        field.save(f"{kind}.mp4", ContentFile(path.read_bytes()), save=False)
    video.raw_video_hash = get_file_hash(video.raw_file)
    video.processed_video_hash = get_file_hash(video.processed_file)
    video.save()
    return video


@pytest.mark.parametrize("kind", ["raw", "processed", "both"])
@pytest.mark.parametrize("existing_frames", [False, True])
def test_mapping_backfills_exact_generation_coordinates_once(
    video: VideoFile,
    kind: str,
    existing_frames: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if kind == "raw":
        video.processed_file.name = ""
        video.processed_video_hash = None
    elif kind == "processed":
        video.raw_file.name = ""
    video.save()
    ids: list[int] = []
    if existing_frames:
        Frame.objects.bulk_create(
            [Frame(video=video, frame_number=index) for index in range(4)]
        )
        ids = list(video.frames.order_by("frame_number").values_list("pk", flat=True))
    origin = 2.0 if kind == "raw" else 4.0
    index = load_video_timeline_index(video)
    assert index.frame_numbers_to_seconds([0, 1, 2, 3]) == [
        origin,
        origin + 0.15,
        origin + 0.4,
        origin + 0.75,
    ]
    assert video.frame_count == 4
    if ids:
        assert (
            list(video.frames.order_by("frame_number").values_list("pk", flat=True))
            == ids
        )
    payload = VideoFileMetaPayload.model_validate(video.meta)
    assert payload.source_timeline is not None
    assert payload.source_timeline.artifact_kind == (
        "raw" if kind == "raw" else "processed"
    )
    assert payload.video_provenance_recovery is None
    assert payload.video_frame_provenance is None
    assert artifact_frame_timestamps(
        video, file_type="raw" if kind == "raw" else "processed"
    ) == (origin, origin + 0.15, origin + 0.4, origin + 0.75)
    if kind == "both":
        with pytest.raises(ValueError, match="unambiguous canonical timestamp"):
            artifact_frame_timestamps(video, file_type="raw")
    field = video.raw_file if kind == "raw" else video.processed_file
    assert Path(field.path).read_bytes().startswith(MAGIC)
    probe = Mock(side_effect=AssertionError("Repeated reads must not probe"))
    monkeypatch.setattr(service, "probe_video_artifact", probe)
    assert video.frame_number_to_s(1) == origin + 0.15
    probe.assert_not_called()


@pytest.mark.parametrize("failure", ["segments", "probe"])
def test_failed_backfill_is_atomic(
    video: VideoFile, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if failure == "segments":
        LabelVideoSegment.objects.create(
            video_file=video, start_frame_number=0, end_frame_number=2
        )
    else:
        monkeypatch.setattr(
            service,
            "probe_video_artifact",
            Mock(side_effect=ValueError("probe failed")),
        )
    before = list(video.frames.values_list("pk", "frame_number", "timestamp"))
    with pytest.raises(ValueError):
        load_video_timeline_index(video)
    video.refresh_from_db()
    assert not (video.meta or {}).get("source_timeline")
    assert list(video.frames.values_list("pk", "frame_number", "timestamp")) == before


@pytest.mark.parametrize("playback", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_backfill_releases_only_its_owned_lease(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch, playback: bool, failure: bool
) -> None:
    if playback:
        operation_gate.create_video_stream_lease(video, file_type="processed")
    previous = list(MediaOperationLease.objects.filter(video=video).values())
    actual_probe = service.probe_video_artifact

    def probe(path: Path) -> VideoArtifactProbe:
        assert (
            MediaOperationLease.objects.filter(video=video).count() == len(previous) + 1
        )
        if failure:
            raise ValueError("capture failed")
        return actual_probe(path)

    monkeypatch.setattr(service, "probe_video_artifact", probe)
    if failure:
        with pytest.raises(ValueError, match="capture failed"):
            service.backfill_missing_video_timestamps(video)
        assert not video.frames.exists()
    else:
        service.backfill_missing_video_timestamps(video)
        assert video.frames.count() == 4
    assert list(MediaOperationLease.objects.filter(video=video).values()) == previous
    if not playback:
        with operation_gate.video_artifact_mutation(video_id=video.pk):
            pass


@pytest.mark.parametrize(
    "blocker", ["transcode", "artifact_write", "expired_writer", "cleanup"]
)
def test_backfill_preserves_writer_and_cleanup_admission(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    if blocker == "cleanup":
        monkeypatch.setattr(
            StorageRotationCleanupReceipt.objects,
            "filter",
            Mock(return_value=Mock(exists=Mock(return_value=True))),
        )
    else:
        MediaOperationLease.objects.create(
            video=video,
            lease_type="artifact_write" if blocker == "expired_writer" else blocker,
            expires_at=timezone.now()
            + timedelta(seconds=-1 if blocker == "expired_writer" else 120),
        )
    previous = list(MediaOperationLease.objects.filter(video=video).values())
    probe = Mock(side_effect=AssertionError("Blocked recovery must not decode media"))
    monkeypatch.setattr(service, "probe_video_artifact", probe)
    with pytest.raises(operation_gate.MediaOperationDeferred):
        service.backfill_missing_video_timestamps(video)
    probe.assert_not_called()
    assert list(MediaOperationLease.objects.filter(video=video).values()) == previous
    assert not video.frames.exists()


@pytest.mark.django_db(transaction=True)
def test_backfill_row_lock_excludes_writer_until_capture_finishes(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent row-lock verification requires PostgreSQL")
    capturing, finish = Event(), Event()
    actual_probe = service.probe_video_artifact

    def probe(path: Path) -> VideoArtifactProbe:
        capturing.set()
        assert finish.wait(timeout=10)
        return actual_probe(path)

    def recover() -> None:
        close_old_connections()
        try:
            service.backfill_missing_video_timestamps(
                VideoFile.objects.get(pk=video.pk)
            )
        finally:
            close_old_connections()

    monkeypatch.setattr(service, "probe_video_artifact", probe)
    with ThreadPoolExecutor(max_workers=1) as executor:
        recovery = executor.submit(recover)
        try:
            assert capturing.wait(timeout=10)
            with (
                pytest.raises(OperationalError, match="lock timeout"),
                transaction.atomic(),
            ):
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '100ms'")
                operation_gate.acquire_video_transcode_lease(video_id=video.pk)
        finally:
            finish.set()
        recovery.result(timeout=30)
    assert not MediaOperationLease.objects.filter(video=video).exists()
    claim = operation_gate.acquire_video_transcode_lease(video_id=video.pk)
    operation_gate.release_video_transcode_lease(claim)


@pytest.mark.parametrize("processed", [False, True])
def test_legacy_recording_hash_does_not_block_current_media_timestamps(
    video: VideoFile, processed: bool
) -> None:
    video.raw_video_hash = "f" * 64
    if not processed:
        video.processed_file.name = ""
        video.processed_video_hash = None
    video.save()
    Frame.objects.bulk_create(
        [Frame(video=video, frame_number=index) for index in range(4)]
    )
    identities = list(
        video.frames.order_by("frame_number").values_list("pk", flat=True)
    )
    service.backfill_missing_video_timestamps(video)
    assert (
        list(video.frames.order_by("frame_number").values_list("pk", flat=True))
        == identities
    )
    assert load_video_timeline_index(video).frame_number_to_seconds(1) == (
        4.15 if processed else 2.15
    )
    payload = VideoFileMetaPayload.model_validate(video.meta)
    assert payload.source_timeline is not None
    kind = "processed" if processed else "raw"
    assert payload.source_timeline.artifact_kind == kind
    assert payload.source_timeline.content_hash == (
        video.processed_video_hash if processed else video.raw_video_hash
    )
    assert video.raw_video_hash == "f" * 64
    assert {
        entry.artifact_kind for entry in payload.canonical_timeline_history or []
    } == {kind}
    if processed:
        with pytest.raises(ValueError, match="unambiguous canonical timestamp"):
            artifact_frame_timestamps(video, file_type="raw")


@pytest.mark.parametrize("field", ["timestamp", "presentation_timestamp"])
def test_conflicting_partial_timestamps_are_not_overwritten(
    video: VideoFile, field: str
) -> None:
    Frame.objects.create(video=video, frame_number=0, **{field: 9})
    with pytest.raises(service.VideoTimelineMappingError):
        load_video_timeline_index(video)
    with pytest.raises(ValueError):
        artifact_frame_timestamps(video, file_type="processed")
    assert getattr(video.frames.get(), field) == 9
    video.refresh_from_db()
    assert not (video.meta or {}).get("source_timeline")


@pytest.mark.parametrize("last_present", [False, True])
def test_segment_end_clamping_reuses_index_and_requires_persisted_last_frame(
    video: VideoFile, last_present: bool
) -> None:
    from endoreg_db.services.annotations.segment_validation_workflow import (
        resolve_segment_validation_frames,
    )

    service.backfill_missing_video_timestamps(video)
    if not last_present:
        video.frames.filter(frame_number=3).update(timestamp=None)
    index = load_video_timeline_index(video)
    with CaptureQueriesContext(connection) as queries:
        if last_present:
            assert resolve_segment_validation_frames(
                video, index, start_time=4.0, end_time=100.0
            ) == (0, 3)
        else:
            with pytest.raises(
                service.VideoTimelineMappingError, match="last persisted"
            ):
                resolve_segment_validation_frames(
                    video, index, start_time=4.0, end_time=100.0
                )
    assert len(queries) == 0


@pytest.mark.parametrize(
    "missing", ["timestamp", "presentation_timestamp", "row", "source", "history"]
)
@pytest.mark.parametrize("processed", [False, True])
def test_partial_coordinates_reconcile_without_changing_saved_values(
    video: VideoFile, missing: str, processed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.services.video_files.timeline_backfill import (
        backfill_video_timelines,
    )

    if not processed:
        video.processed_file.name = ""
        video.processed_video_hash = None
        video.save(update_fields=["processed_file", "processed_video_hash"])
    service.backfill_missing_video_timestamps(video)
    original = list(
        video.frames.order_by("frame_number").values_list(
            "pk", "frame_number", "timestamp", "presentation_timestamp"
        )
    )
    if missing in {"source", "history"}:
        metadata_field = (
            "source_timeline" if missing == "source" else "canonical_timeline_history"
        )
        video.meta = {
            key: value
            for key, value in (video.meta or {}).items()
            if key != metadata_field
        }
        video.save(update_fields=["meta"])
    elif missing == "row":
        video.frames.filter(frame_number=1).delete()
    else:
        video.frames.filter(frame_number=1).update(**{missing: None})
    before = list(video.frames.values_list("pk", "timestamp", "presentation_timestamp"))
    assert (
        backfill_video_timelines(apply=False, video_ids=[video.pk]).entries[0].status
        == "candidate"
    )
    assert (
        list(video.frames.values_list("pk", "timestamp", "presentation_timestamp"))
        == before
    )
    assert (
        backfill_video_timelines(apply=True, video_ids=[video.pk]).entries[0].status
        == "backfilled"
    )
    restored = list(
        video.frames.order_by("frame_number").values_list(
            "pk", "frame_number", "timestamp", "presentation_timestamp"
        )
    )
    assert [row[1:] for row in restored] == [row[1:] for row in original]
    assert all(
        row[0] == original[index][0]
        for index, row in enumerate(restored)
        if missing != "row" or index != 1
    )
    monkeypatch.setattr(
        service,
        "probe_video_artifact",
        Mock(side_effect=AssertionError("Duplicate recovery must not decode")),
    )
    assert (
        backfill_video_timelines(apply=True, video_ids=[video.pk]).entries[0].status
        == "ready"
    )


@pytest.mark.parametrize("numbers", [[0, 1], [0, 1, 2, 3, 4, 5], [0, 1, 2, 4]])
def test_decoded_pts_reconciles_unannotated_legacy_frame_count(
    video: VideoFile, numbers: list[int]
) -> None:
    Frame.objects.bulk_create([Frame(video=video, frame_number=i) for i in numbers])
    kept = dict(
        video.frames.filter(frame_number__lt=4).values_list("frame_number", "pk")
    )
    service.backfill_missing_video_timestamps(video)
    assert video.frame_count == 4
    assert list(
        video.frames.order_by("frame_number").values_list("timestamp", flat=True)
    ) == [4, 4.15, 4.4, 4.75]
    assert all(video.frames.get(frame_number=n).pk == pk for n, pk in kept.items())


def test_count_reconciliation_preserves_annotated_coordinates(video: VideoFile) -> None:
    Frame.objects.create(video=video, frame_number=0, is_extracted=True)
    with pytest.raises(service.VideoTimelineMappingError, match="timestamp remapping"):
        service.backfill_missing_video_timestamps(video)
    assert video.frames.count() == 1
    assert video.frames.get().timestamp is None


def test_timestamp_only_recovery_still_rejects_tampered_ciphertext(
    video: VideoFile,
) -> None:
    from endoreg_db.utils.file_operations import atomic_write_file
    from endoreg_db.utils.paths import get_runtime_paths

    path = Path(video.processed_file.path)
    ciphertext = path.read_bytes()
    atomic_write_file(
        destination=path,
        content=(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]),),
        file_mode=0o600,
    )
    temporary_before = set(get_runtime_paths().transcoding.iterdir())
    with pytest.raises(Exception):
        service.backfill_missing_video_timestamps(video)
    video.refresh_from_db()
    assert not video.frames.exists()
    assert not (video.meta or {}).get("source_timeline")
    assert set(get_runtime_paths().transcoding.iterdir()) == temporary_before


@pytest.mark.parametrize("entrypoint", ["decoder", "download"])
@pytest.mark.parametrize("raw_history", [False, True])
def test_missing_timestamps_recover_at_read_entrypoints(
    video: VideoFile, entrypoint: str, raw_history: bool
) -> None:
    if raw_history:
        processed_name = video.processed_file.name
        video.processed_file.name = ""
        video.save(update_fields=["processed_file"])
        load_video_timeline_index(video)
        payload = VideoFileMetaPayload.model_validate(video.meta)
        video.meta = {
            "canonical_timeline_history": [
                entry.model_dump(mode="json")
                for entry in payload.canonical_timeline_history or []
                if entry.artifact_kind == "raw"
            ]
        }
        video.processed_file.name = processed_name
        video.save(update_fields=["meta", "processed_file"])
        video.frames.all().delete()
    if entrypoint == "decoder":
        assert artifact_frame_timestamps(video, file_type="processed")[0] == 4.0
    else:
        assert len(list(timestamp_csv(video, original=False))) == 5
    assert video.frames.count() == 4
    if raw_history:
        assert len(list(timestamp_csv(video, original=True))) == 5
    else:
        with pytest.raises(ValueError, match="Zeitstempelfolge"):
            list(timestamp_csv(video, original=True))


def test_stale_reader_reuses_completed_backfill(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = VideoFile.objects.get(pk=video.pk)
    load_video_timeline_index(video)
    monkeypatch.setattr(
        service,
        "probe_video_artifact",
        Mock(side_effect=AssertionError("Must reuse completed backfill")),
    )
    assert load_video_timeline_index(stale).frame_number_to_seconds(1) == 4.15


def test_decoder_can_backfill_legacy_rate_only_metadata(video: VideoFile) -> None:
    video.meta = constant_rate_video_metadata(fps_num=10, frame_count=4)
    video.save(update_fields=["meta"])
    assert artifact_frame_timestamps(video, file_type="processed") == (
        4.0,
        4.15,
        4.4,
        4.75,
    )


def test_populated_timeline_index_keeps_one_frame_query(video: VideoFile) -> None:
    load_video_timeline_index(video)
    with CaptureQueriesContext(connection) as queries:
        index = load_video_timeline_index(video)
        assert index.frame_number_to_seconds(1) == 4.15
    assert len(queries) == 1


def test_backfill_preserves_existing_segment_boundaries(video: VideoFile) -> None:
    Frame.objects.bulk_create(
        [Frame(video=video, frame_number=index) for index in range(4)]
    )
    segment = LabelVideoSegment.objects.create(
        video_file=video, start_frame_number=1, end_frame_number=3
    )
    load_video_timeline_index(video)
    segment.refresh_from_db()
    assert (segment.start_frame_number, segment.end_frame_number) == (1, 3)


@pytest.mark.parametrize("entrypoint", ["frame", "seconds", "neighborhood", "index"])
def test_existing_timeline_without_frame_timestamps_recovers(
    video: VideoFile, entrypoint: str
) -> None:
    load_video_timeline_index(video)
    video.frames.update(timestamp=None, presentation_timestamp=None)
    if entrypoint == "frame":
        assert video.frame_number_to_s(1) == 4.15
    elif entrypoint == "seconds":
        assert video_seconds_to_frame_number(video, 4.15) == 1
    elif entrypoint == "neighborhood":
        assert get_video_frame_neighborhood(video, 4.15).current.frame_number == 1
    else:
        assert load_video_timeline_index(video).frame_number_to_seconds(1) == 4.15
    assert video.frames.filter(timestamp__isnull=True).count() == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_readers_capture_each_generation_once(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = Barrier(2)
    probe = Mock(wraps=service.probe_video_artifact)
    monkeypatch.setattr(service, "probe_video_artifact", probe)

    def resolve() -> float:
        close_old_connections()
        try:
            current = VideoFile.objects.get(pk=video.pk)
            start.wait(timeout=10)
            return load_video_timeline_index(current).frame_number_to_seconds(1)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(resolve)
        second = executor.submit(resolve)
        assert first.result(timeout=30) == second.result(timeout=30) == 4.15
    assert probe.call_count == 1
    assert video.frames.count() == 4


@pytest.mark.parametrize("processed", [False, True])
def test_missing_canonical_media_raises_timeline_error_without_writes(
    video: VideoFile, processed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if processed:
        video.processed_file.name = ".generations/missing-processed.mp4"
    else:
        video.processed_file.name = ""
        video.processed_video_hash = None
        video.raw_file.name = ".generations/missing-raw.mp4"
    video.save()
    Frame.objects.create(video=video, frame_number=0)
    before = list(video.frames.values_list("pk", "timestamp", "presentation_timestamp"))
    metadata = video.meta
    count = video.frame_count
    probe = Mock(
        side_effect=AssertionError("Missing media must not probe another generation")
    )
    monkeypatch.setattr(service, "probe_video_artifact", probe)

    with pytest.raises(
        service.VideoTimelineMappingError, match="Canonical media is missing"
    ) as error:
        load_video_timeline_index(video)

    assert ".generations" not in str(error.value)
    video.refresh_from_db()
    assert video.meta == metadata
    assert video.frame_count == count
    assert (
        list(video.frames.values_list("pk", "timestamp", "presentation_timestamp"))
        == before
    )
    probe.assert_not_called()
