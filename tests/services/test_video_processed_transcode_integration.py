from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files import File

from endoreg_db.models import Center, VideoFile
from endoreg_db.services import hls_media, video_processed_transcode as service
from endoreg_db.services.video_files.queries import (
    get_video_by_content_hash,
    video_hash_exists,
)
from endoreg_db.services.video_files.frames import initialize_video_frames
from endoreg_db.services.video_storage_normalization import (
    persist_video_source_timeline,
)
from endoreg_db.utils import ffmpeg_wrapper, transcode_execution
from endoreg_db.utils.transcode_execution import get_stream_info as real_stream_info
from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.encryption.encrypted import MAGIC


@pytest.mark.django_db(transaction=True)
@pytest.mark.ffmpeg
def test_real_downsizing_preserves_import_identity_frames_and_playback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Undo the global probe substitutes: this regression must inspect real bytes.
    monkeypatch.setattr(ffmpeg_wrapper, "get_stream_info", real_stream_info)
    monkeypatch.setattr(transcode_execution, "get_stream_info", real_stream_info)
    source = Path(__file__).parents[1] / "assets" / "test.mp4"
    source_hash = get_file_hash(source)
    probe = service.probe_video_artifact(source)
    center = Center.objects.create(
        name="real-downsizing", display_name="Real downsizing"
    )
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=source_hash,
        fps=probe.timeline.fps,
        duration=probe.timeline.duration_seconds,
        frame_count=probe.timeline.frame_count,
    )
    with source.open("rb") as stream:
        video.raw_file.save(f"{source_hash}.mp4", File(stream), save=True)
    save_local_file(video.processed_file, source, name=f"{source_hash}.mp4", save=False)
    video.processed_video_hash = source_hash
    video.save(update_fields=["processed_file", "processed_video_hash"])
    initialize_video_frames(video)
    persist_video_source_timeline(video, source)
    identity = (video.pk, video.uuid, video.raw_video_hash)
    frames = list(
        video.frames.order_by("frame_number").values_list(
            "pk", "frame_number", "timestamp"
        )
    )
    old_path = Path(video.processed_file.path)
    raw_path = Path(video.raw_file.path)

    result = service.transcode_processed_video_for_storage_pressure(
        video,
        apply=True,
        force_cpu=True,
        expected_processed_name=str(video.processed_file.name),
        expected_processed_hash=source_hash,
    )

    assert result.status == "changed", result
    assert result.published
    assert 0 < result.new_size < result.old_size
    video.refresh_from_db()
    assert (video.pk, video.uuid, video.raw_video_hash) == identity
    assert video_hash_exists(source_hash)
    assert get_video_by_content_hash(source_hash).pk == video.pk
    assert video.processed_video_hash == result.new_hash != source_hash
    assert get_file_hash(video.processed_file) == result.new_hash
    assert get_file_hash(video.raw_file) == source_hash
    assert (
        list(
            video.frames.order_by("frame_number").values_list(
                "pk", "frame_number", "timestamp"
            )
        )
        == frames
    )
    with Path(video.processed_file.path).open("rb") as encrypted:
        assert encrypted.read(len(MAGIC)) == MAGIC
    assert raw_path.exists()
    assert not old_path.exists()
    artifact = hls_media.get_ready_hls_artifact(video=video, artifact_kind="processed")
    assert artifact is not None
    assert artifact.source_content_hash == result.new_hash
    assert artifact.source_file_name == video.processed_file.name
