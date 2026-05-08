from __future__ import annotations

from types import SimpleNamespace

from endoreg_db.export.frames import export_frames_with_labels as export_module


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
            name=processed_path.relative_to(storage_dir).as_posix(),
        ),
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
