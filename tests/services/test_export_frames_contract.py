from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test.utils import override_settings

from endoreg_db.export.frames import export_frames_with_labels as export_module
from endoreg_db.utils.filesystem.file_operations import atomic_write_file


class _FakeValuesList:
    def __init__(self, values):
        self._values = values

    def distinct(self):
        return self

    def order_by(self, *args, **kwargs):  # noqa: ARG002
        return self._values


class _FakeAnnotations:
    def __init__(self, video_id: int):
        self._video_id = video_id

    def values_list(self, *args, **kwargs):  # noqa: ARG002
        return _FakeValuesList([self._video_id])


class _FakeFrameAnnotations:
    def __init__(self, annotation):
        self._annotation = annotation

    def iterator(self):
        yield self._annotation


def test_export_videos_prefers_processed_artifact_over_raw(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    raw_path = storage_dir / "sensitive_videos" / "raw-source.mp4"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"raw-video-bytes")

    processed_path = storage_dir / "anonymized_videos" / "processed-final.mp4"
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_bytes(b"processed-video-bytes")

    video = SimpleNamespace(
        pk=7,
        video_hash="raw-hash",
        raw_file=SimpleNamespace(
            name=raw_path.relative_to(storage_dir).as_posix(),
        ),
        processed_file=SimpleNamespace(
            name=processed_path.relative_to(storage_dir).as_posix(),
        ),
        active_file=SimpleNamespace(
            name=raw_path.relative_to(storage_dir).as_posix(),
        ),
        state=SimpleNamespace(anonymization_validated=True),
    )

    output_dir = tmp_path / "exported"
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        export_module,
        "resolve_existing_protected_media_path",
        lambda name: storage_dir / name if (storage_dir / name).exists() else None,
    )
    monkeypatch.setattr(
        export_module.VideoFile.objects,
        "filter",
        lambda **kwargs: [video],
    )

    exported_count = export_module._export_videos_from_annotations(
        _FakeAnnotations(video.pk),
        output_dir=output_dir,
    )

    assert exported_count == 1
    exported_file = output_dir / f"video_{video.pk}_{video.video_hash}.mp4"
    assert exported_file.read_bytes() == b"processed-video-bytes"


def test_export_config_defaults_are_safe():
    config = export_module.export_config(output_path="frames.csv")

    assert config.export_frames is True
    assert config.export_videos is False
    assert config.use_export_flags is True
    assert config.only_validated is True


def test_export_videos_rejects_unvalidated_media(tmp_path, monkeypatch):
    video = SimpleNamespace(
        pk=7,
        video_hash="raw-hash",
        processed_file=SimpleNamespace(name="processed-final.mp4"),
        state=SimpleNamespace(anonymization_validated=False),
    )
    monkeypatch.setattr(
        export_module.VideoFile.objects,
        "filter",
        lambda **kwargs: [video],
    )

    with pytest.raises(ValueError, match="not human anonymization validated"):
        export_module._export_videos_from_annotations(
            _FakeAnnotations(video.pk),
            output_dir=tmp_path,
        )


def test_export_videos_rejects_failed_lost_media():
    video = SimpleNamespace(
        pk=7,
        state=SimpleNamespace(
            processing_error=True,
            anonymization_validated=True,
        ),
    )

    with pytest.raises(ValueError, match="failed/lost"):
        export_module._assert_video_media_export_ready(video)


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_asset_export_forces_processed_transcode():
    config = export_module.export_config(
        output_path="frames.csv",
        center_key="center-a",
        export_frames=True,
        transcode_frames=False,
        transcode_overwrite=False,
        use_frame_pk_paths=False,
    )

    normalized = export_module._normalize_export_config(config)

    assert normalized.transcode_frames is True
    assert normalized.transcode_overwrite is True
    assert normalized.use_frame_pk_paths is True


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_asset_export_rejects_unready_media(tmp_path):
    video = SimpleNamespace(
        pk=11,
        state=SimpleNamespace(
            anonymization_validated=True,
            outside_segments_removed=True,
            ready_for_export=False,
            processed_file_sha256="",
        ),
    )
    frame = SimpleNamespace(
        pk=4,
        video=video,
        relative_path="frames/frame_4.jpg",
        file_path="frames/frame_4.jpg",
    )
    annotation = SimpleNamespace(frame=frame)

    with pytest.raises(ValueError, match="ready_for_export"):
        export_module._export_frames_from_annotations(
            _FakeFrameAnnotations(annotation),
            output_dir=tmp_path,
            use_frame_pk_paths=True,
            frame_ext="jpg",
            generated_frame_root=tmp_path / "generated_frames",
        )


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_transcode_uses_export_scoped_frame_directory(tmp_path, monkeypatch):
    source_path = tmp_path / "processed.mp4"
    atomic_write_file(
        destination=source_path,
        content=[b"processed"],
        required_bytes=len(b"processed"),
    )
    captured = {}

    def fail_canonical_dir():
        raise AssertionError("canonical frame dir must not be used")

    video = SimpleNamespace(pk=21, get_frame_dir_path=fail_canonical_dir)

    monkeypatch.setattr(
        export_module,
        "_assert_video_media_export_ready",
        lambda video: None,
    )
    monkeypatch.setattr(
        export_module,
        "_resolve_processed_video_source_path",
        lambda video: source_path,
    )

    def fake_extract(
        video,
        *,
        source_path,
        frame_dir,
        frame_pks,
        fps,
        quality,
        ext,
        overwrite,
    ):
        captured["frame_dir"] = frame_dir
        captured["source_path"] = source_path

    monkeypatch.setattr(
        export_module,
        "_extract_and_move_transcoded_frames",
        fake_extract,
    )

    export_root = tmp_path / "export-generated"
    export_module._transcode_video_to_frame_dir(
        video,
        frame_pks={1},
        fps=25.0,
        quality=2,
        ext="jpg",
        overwrite=True,
        export_frame_root=export_root,
    )

    assert captured["frame_dir"] == export_root / "video_21"
    assert captured["source_path"] == source_path


@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_local_frame_source_does_not_fallback_to_canonical_storage(tmp_path):
    def fail_canonical_dir():
        raise AssertionError("canonical frame dir must not be used")

    video = SimpleNamespace(pk=31, get_frame_dir_path=fail_canonical_dir)
    frame = SimpleNamespace(video=video, file_path="canonical/frame_5.jpg")
    generated_root = tmp_path / "generated"

    source_path = export_module._resolve_frame_source_path(
        frame,
        frame_relative_path="frame_5.jpg",
        use_frame_pk_paths=True,
        frame_ext="jpg",
        generated_frame_root=generated_root,
    )

    assert source_path == generated_root / "video_31" / "frame_5.jpg"
