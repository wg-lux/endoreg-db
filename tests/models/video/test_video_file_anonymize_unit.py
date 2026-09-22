from __future__ import annotations
from endoreg_db.utils.paths import get_runtime_paths

# pyright: reportPrivateUsage=false

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import NoReturn, Protocol
from unittest.mock import Mock
import pytest
from pytest import MonkeyPatch
from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    roi_box_to_legacy_dict,
)

import endoreg_db.models.media.video.video_file as video_file_module
from endoreg_db.models import (
    Center,
    Label,
    LabelVideoSegment,
    SensitiveMeta,
    VideoFile,
)
from endoreg_db.services.video_files import _anonymization as anonymize_module


class _NameWritableField(Protocol):
    name: str


@pytest.mark.django_db
def test_anonymize_uses_streamed_mask_without_full_frame_extraction(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    center = Center.objects.create(name="stream-anonym-center")
    sensitive_meta = SensitiveMeta.objects.create(center=center)
    sensitive_state = sensitive_meta.get_or_create_state()
    sensitive_state.dob_verified = True
    sensitive_state.names_verified = True
    sensitive_state.save(update_fields=["dob_verified", "names_verified"])
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash="stream-anonym-hash",
        raw_file="sensitive_videos/raw.mp4",
        sensitive_meta=sensitive_meta,
        frame_count=250,
        fps=25,
        duration=10,
    )
    outside_label = Label.objects.create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video,
        label=outside_label,
        start_frame_number=10,
        end_frame_number=20,
    )
    state = video.get_or_create_state()
    state.frames_extracted = False
    state.save(update_fields=["frames_extracted"])

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw-video")
    captured: dict[str, object] = {}

    class _RawContext:
        def __enter__(self) -> Path:
            return raw_path

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            return False

    def fail_extract_frames(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("streamed anonymize must not extract all frames")

    def fake_mask(
        input_path: Path,
        output_path: Path,
        *,
        endo_roi: RoiBoxCore,
        intervals: Sequence[tuple[int, int]],
    ) -> Path:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["endo_roi"] = endo_roi
        captured["intervals"] = intervals
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"processed-video")
        return output_path

    def fake_save_local_file(
        field_file: _NameWritableField,
        source_path: Path,
        *,
        name: str,
        save: bool = False,
        overwrite: bool = False,
    ) -> None:
        captured["saved_source_path"] = source_path
        captured["saved_name"] = name
        field_file.name = name

    monkeypatch.setattr(VideoFile, "extract_frames", fail_extract_frames)

    def fake_ensure_local_raw_file(self: VideoFile) -> _RawContext:
        return _RawContext()

    monkeypatch.setattr(VideoFile, "ensure_local_raw_file", fake_ensure_local_raw_file)

    def fake_get_endo_roi(self: VideoFile) -> dict[str, int]:
        return {"x": 1, "y": 2, "width": 30, "height": 40}

    monkeypatch.setattr(
        VideoFile,
        "get_endo_roi",
        fake_get_endo_roi,
    )
    monkeypatch.setattr(
        anonymize_module,
        "mask_video_to_roi_and_blacken_intervals",
        fake_mask,
    )

    def fake_get_file_hash(path: Path) -> str:
        _ = path
        return "processed-hash"

    monkeypatch.setattr(
        anonymize_module,
        "get_file_hash",
        fake_get_file_hash,
    )
    monkeypatch.setattr(anonymize_module, "save_local_file", fake_save_local_file)
    monkeypatch.setattr(
        type(video.processed_file), "get_hash", Mock(return_value="processed-hash")
    )
    from tests.services.test_video_processed_transcode_encryption import probe

    def fake_probe(path: Path):
        return probe()

    def fake_ready(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(anonymize_module, "probe_video_artifact", fake_probe)
    monkeypatch.setattr(anonymize_module, "ensure_video_hls", fake_ready)

    def fake_sync_video_streamable_artifacts(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        anonymize_module,
        "sync_video_streamable_artifacts",
        fake_sync_video_streamable_artifacts,
    )

    assert anonymize_module._anonymize(video, delete_original_raw=False) is True

    assert captured["input_path"] == raw_path
    output_path = captured["output_path"]
    assert isinstance(output_path, Path)
    assert output_path.parent == get_runtime_paths().transcoding
    assert output_path.name.startswith("stream-anonym-hash.")
    assert not output_path.exists()
    endo_roi = captured["endo_roi"]
    assert isinstance(endo_roi, RoiBoxCore)
    assert roi_box_to_legacy_dict(endo_roi) == {
        "x": 1,
        "y": 2,
        "width": 30,
        "height": 40,
    }
    assert captured["intervals"] == [(10, 20)]
    saved_name = captured["saved_name"]
    assert isinstance(saved_name, str)
    assert saved_name == f"processed_videos_final/{output_path.name}"
    video.refresh_from_db()
    state.refresh_from_db()
    assert video.processed_video_hash == "processed-hash"
    assert state.anonymized is True
    assert state.frames_extracted is False


def test_cleanup_raw_assets_deletes_raw_paths_and_updates_state(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    raw_file_path = tmp_path / "raw.mp4"
    raw_file_path.write_bytes(b"raw-video")

    raw_frame_dir = tmp_path / "frames"
    raw_frame_dir.mkdir(parents=True, exist_ok=True)
    (raw_frame_dir / "frame_0000001.jpg").write_bytes(b"frame")

    class _FakeState:
        def __init__(self) -> None:
            self.frames_extracted = True
            self.saved_update_fields: list[str] | None = None

        def save(self, update_fields: list[str] | None = None) -> None:
            self.saved_update_fields = update_fields

    fake_state = _FakeState()
    deleted_storage_names: list[str] = []

    class _FakeStorage:
        def delete(self, name: str) -> None:
            deleted_storage_names.append(name)
            raw_file_path.unlink(missing_ok=True)

    class _FakeFieldFile:
        name = ""
        storage = _FakeStorage()

        def delete(self, *, save: bool = False) -> None:
            self.storage.delete(self.name)
            self.name = ""

    fake_video = SimpleNamespace(
        state=fake_state,
        raw_file=_FakeFieldFile(),
        get_or_create_state=lambda: fake_state,
    )

    class _FakeQuerySet:
        def __init__(self, result: object) -> None:
            self.result = result
            self.filter_kwargs: dict[str, object] | None = None

        def select_related(self, *args: object, **kwargs: object) -> _FakeQuerySet:
            return self

        def filter(self, **kwargs: object) -> _FakeQuerySet:
            self.filter_kwargs = dict(kwargs)
            return self

        def first(self) -> object:
            return self.result

    fake_queryset = _FakeQuerySet(fake_video)

    def delete() -> None:
        return None

    fake_video_model = SimpleNamespace(objects=fake_queryset, delete=delete())
    monkeypatch.setattr(video_file_module, "VideoFile", fake_video_model)

    anonymize_module._cleanup_raw_assets(
        raw_video_hash="hash-cleanup",
        raw_file_name="sensitive_videos/raw.mp4",
        raw_frame_dir=raw_frame_dir,
    )

    assert deleted_storage_names == ["sensitive_videos/raw.mp4"]
    assert not raw_file_path.exists()
    assert not raw_frame_dir.exists()
    assert fake_queryset.filter_kwargs == {"raw_video_hash": "hash-cleanup"}
    assert fake_state.frames_extracted is False
    assert fake_state.saved_update_fields == ["frames_extracted"]
