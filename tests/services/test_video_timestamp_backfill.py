from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext

from endoreg_db.models import Center, Frame, LabelVideoSegment, VideoFile
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
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
        assert artifact_frame_timestamps(video, file_type="raw") == (2, 2.15, 2.4, 2.75)
    field = video.raw_file if kind == "raw" else video.processed_file
    assert Path(field.path).read_bytes().startswith(MAGIC)
    probe = Mock(side_effect=AssertionError("Repeated reads must not probe"))
    monkeypatch.setattr(service, "probe_video_artifact", probe)
    assert video.frame_number_to_s(1) == origin + 0.15
    probe.assert_not_called()


@pytest.mark.parametrize("failure", ["hash", "count", "gap", "segments", "probe"])
def test_failed_backfill_is_atomic(
    video: VideoFile, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if failure == "hash":
        video.processed_video_hash = "f" * 64
        video.save()
    elif failure in {"count", "gap"}:
        Frame.objects.bulk_create(
            [
                Frame(video=video, frame_number=i)
                for i in ([0, 1] if failure == "count" else [0, 1, 2, 4])
            ]
        )
    elif failure == "segments":
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


def test_partial_timestamps_are_not_overwritten(
    video: VideoFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    Frame.objects.create(video=video, frame_number=0, timestamp=9.0)
    probe = Mock(
        side_effect=AssertionError("Partial coordinates must not be backfilled")
    )
    monkeypatch.setattr(service, "probe_video_artifact", probe)
    with pytest.raises(
        service.VideoTimelineMappingError, match="persisted source timeline"
    ):
        load_video_timeline_index(video)
    with pytest.raises(ValueError, match="unambiguous canonical timestamp sequence"):
        artifact_frame_timestamps(video, file_type="processed")
    assert video.frames.get().timestamp == 9.0
    probe.assert_not_called()


@pytest.mark.parametrize("entrypoint", ["decoder", "download"])
@pytest.mark.parametrize("raw_history", [False, True])
def test_missing_timestamps_recover_at_read_entrypoints(
    video: VideoFile, entrypoint: str, raw_history: bool
) -> None:
    if raw_history:
        load_video_timeline_index(video)
        payload = VideoFileMetaPayload.model_validate(video.meta)
        video.meta = {
            "canonical_timeline_history": [
                entry.model_dump(mode="json")
                for entry in payload.canonical_timeline_history or []
                if entry.artifact_kind == "raw"
            ]
        }
        video.save(update_fields=["meta"])
        video.frames.all().delete()
    if entrypoint == "decoder":
        assert artifact_frame_timestamps(video, file_type="processed")[0] == 4.0
    else:
        assert len(list(timestamp_csv(video, original=False))) == 5
    assert video.frames.count() == 4
    assert len(list(timestamp_csv(video, original=True))) == 5


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
    assert probe.call_count == 2
    assert video.frames.count() == 4
