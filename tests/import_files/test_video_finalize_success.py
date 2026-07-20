from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest
from pytest import MonkeyPatch

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.state_management import finalize_video_success
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.utils import paths as paths_module


def _runtime_storage_root() -> Path:
    return paths_module.EndoregPathsModel.from_environment().storage


def _normalization_evidence() -> VideoStorageNormalizationEvidence:
    timeline = VideoTimelineContract(
        fps_num=25,
        fps_den=1,
        duration_seconds=10.0,
        frame_count=250,
    )
    probe = VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1920,
        height=1080,
        bit_rate_bps=800_000,
        size_bytes=1_000_000,
        timeline=timeline,
    )
    return VideoStorageNormalizationEvidence(
        profile_name="test",
        normalized_at=datetime.now(UTC),
        source=probe,
        output=probe,
        temporal_equivalent=True,
        storage_compliant=True,
    )


@pytest.mark.unit
def test_ensure_video_hls_materializes_raw_and_processed(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.import_files.file_storage.state_management as state_management_module

    artifact_kinds: list[object] = []

    def record_materialize_video_hls(
        *args: object, **kwargs: object
    ) -> SimpleNamespace:
        artifact_kinds.append(kwargs.get("artifact_kind"))
        return SimpleNamespace(status="materialized")

    monkeypatch.setattr(
        state_management_module,
        "materialize_video_hls",
        record_materialize_video_hls,
        raising=True,
    )
    video = cast(VideoFile, SimpleNamespace(pk=42))
    state_management_module.ensure_video_hls(video, force=True)

    assert artifact_kinds == ["raw", "processed"]


@pytest.mark.unit
def test_finalize_video_success_keeps_only_canonical_raw_and_anonymized(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.import_files.file_storage.cleanup as cleanup_module
    import endoreg_db.import_files.file_storage.state_management as state_management_module

    storage_root = _runtime_storage_root() / "pytest_finalize_video_success"
    sensitive_dir = storage_root / "sensitive_videos"
    anonym_dir = storage_root / "anonymized_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)
    anonym_dir.mkdir(parents=True, exist_ok=True)

    raw_path = sensitive_dir / "video_hash.mp4"
    raw_path.write_bytes(b"canonical-raw")
    sensitive_working_copy = sensitive_dir / "exam.mp4"
    sensitive_working_copy.write_bytes(b"temporary-working-copy")

    temp_anonymized = tmp_path / "processing" / "anon_temp.mp4"
    temp_anonymized.parent.mkdir(parents=True, exist_ok=True)
    temp_anonymized.write_bytes(b"anonymized")
    import_file = tmp_path / "import" / "exam.mp4"
    import_file.parent.mkdir(parents=True, exist_ok=True)
    import_file.write_bytes(b"import")

    class DummyState:
        processing_started = False

        def mark_processing_started(self) -> None:
            self.processing_started = True

        def mark_anonymized(self) -> None:
            self.anonymized = True

        def mark_sensitive_meta_processed(self) -> None:
            self.sensitive_meta_processed = True

        def save(self) -> None:
            return None

    class DummyVideo:
        def __init__(self) -> None:
            self.pk = 1
            self.video_hash = "video_hash"
            self.processed_video_hash = None
            self.processed_file = SimpleNamespace(name=None)
            self.state = DummyState()
            self.meta: dict[str, object] = {}

        def get_raw_file_path(self) -> Path:
            return raw_path

        def save(self) -> None:
            return None

        def get_or_create_state(self) -> object:
            return self.state

    @contextmanager
    def fake_atomic():
        yield

    monkeypatch.setattr(
        state_management_module,
        "VideoFile",
        DummyVideo,
        raising=True,
    )

    def fake_processed_video_dir() -> Path:
        return anonym_dir

    def fail_nuke_transcoding_dir(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("finalize_video_success must not nuke global transcoding")

    monkeypatch.setattr(
        state_management_module,
        "_processed_video_dir",
        fake_processed_video_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "nuke_transcoding_dir",
        fail_nuke_transcoding_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    def fake_get_or_create_for_hash(**kwargs: object) -> None:
        return None

    def fake_get_stream_info(path: str | Path) -> dict[str, object]:
        return {"streams": [{"codec_type": "video"}]}

    def fake_staging_cleanup_roots() -> tuple[Path, ...]:
        return (sensitive_dir,)

    monkeypatch.setattr(
        state_management_module.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(fake_get_or_create_for_hash),
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "get_stream_info",
        fake_get_stream_info,
        raising=True,
    )

    store_calls: list[tuple[Path, str]] = []

    def fake_store_existing_final_file(
        field_file: object,
        final_path: Path,
        *,
        relative_name: str | None = None,
    ) -> str:
        assert relative_name is not None
        stored_path = _runtime_storage_root() / relative_name
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_bytes(final_path.read_bytes())
        setattr(field_file, "name", relative_name)
        store_calls.append((stored_path, relative_name))
        return relative_name

    monkeypatch.setattr(
        state_management_module,
        "_store_existing_final_file",
        fake_store_existing_final_file,
        raising=True,
    )
    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        fake_staging_cleanup_roots,
        raising=True,
    )
    hls_calls: list[int] = []

    def fake_ensure_video_hls(
        video_arg: VideoFile,
        *,
        force: bool = False,
    ) -> None:
        assert force is True
        hls_calls.append(int(video_arg.pk))

    monkeypatch.setattr(
        state_management_module,
        "ensure_video_hls",
        fake_ensure_video_hls,
        raising=True,
    )

    video = DummyVideo()
    ctx = ImportContext(
        file_path=import_file,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.file_hash = "file-hash"
    ctx.current_video = cast(VideoFile, video)
    ctx.sensitive_path = sensitive_working_copy
    ctx.anonymized_path = temp_anonymized
    ctx.storage_normalization_evidence = _normalization_evidence()

    finalize_video_success(ctx)

    final_anonymized = anonym_dir / "video_hash.mp4"
    assert raw_path.exists()
    assert final_anonymized.exists()
    assert not sensitive_working_copy.exists()
    assert video.processed_video_hash == sha256(b"anonymized").hexdigest()
    assert video.processed_file.name.endswith("anonymized_videos/video_hash.mp4")
    assert store_calls == [(final_anonymized, video.processed_file.name)]
    assert hls_calls == [1]


@pytest.mark.unit
def test_finalize_video_success_rejects_unprobeable_final_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.import_files.file_storage.state_management as state_management_module

    storage_root = _runtime_storage_root() / "pytest_finalize_video_invalid_output"
    sensitive_dir = storage_root / "sensitive_videos"
    anonym_dir = storage_root / "anonymized_videos"
    sensitive_dir.mkdir(parents=True, exist_ok=True)
    anonym_dir.mkdir(parents=True, exist_ok=True)

    raw_path = sensitive_dir / "video_hash.mp4"
    raw_path.write_bytes(b"canonical-raw")
    sensitive_working_copy = sensitive_dir / "exam.mp4"
    sensitive_working_copy.write_bytes(b"temporary-working-copy")

    temp_anonymized = tmp_path / "processing" / "anon_temp.mp4"
    temp_anonymized.parent.mkdir(parents=True, exist_ok=True)
    temp_anonymized.write_bytes(b"anonymized")
    import_file = tmp_path / "import" / "exam.mp4"
    import_file.parent.mkdir(parents=True, exist_ok=True)
    import_file.write_bytes(b"import")

    history_calls: list[dict[str, object]] = []

    class DummyState:
        processing_started = False

        def mark_processing_started(self) -> None:
            self.processing_started = True

        def mark_anonymized(self) -> None:
            self.anonymized = True

        def mark_sensitive_meta_processed(self) -> None:
            self.sensitive_meta_processed = True

        def save(self) -> None:
            return None

    class DummyVideo:
        def __init__(self) -> None:
            self.pk = 1
            self.video_hash = "video_hash"
            self.processed_video_hash = None
            self.processed_file = SimpleNamespace(name=None)
            self.state = DummyState()

        def get_raw_file_path(self) -> Path:
            return raw_path

        def save(self) -> None:
            return None

        def get_or_create_state(self) -> object:
            return self.state

    @contextmanager
    def fake_atomic():
        yield

    monkeypatch.setattr(
        state_management_module,
        "VideoFile",
        DummyVideo,
        raising=True,
    )

    def fake_processed_video_dir() -> Path:
        return anonym_dir

    def fail_nuke_transcoding_dir(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("finalize_video_success must not nuke global transcoding")

    monkeypatch.setattr(
        state_management_module,
        "_processed_video_dir",
        fake_processed_video_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "nuke_transcoding_dir",
        fail_nuke_transcoding_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )

    def fake_get_stream_info(path: str | Path) -> None:
        return None

    def fake_get_or_create_for_hash(**kwargs: object) -> None:
        history_calls.append(kwargs)

    monkeypatch.setattr(
        state_management_module,
        "get_stream_info",
        fake_get_stream_info,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(fake_get_or_create_for_hash),
        raising=True,
    )

    video = DummyVideo()
    ctx = ImportContext(
        file_path=import_file,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.file_hash = "file-hash"
    ctx.current_video = cast(VideoFile, video)
    ctx.sensitive_path = sensitive_working_copy
    ctx.anonymized_path = temp_anonymized

    with pytest.raises(RuntimeError, match="ffprobe validation"):
        finalize_video_success(ctx)

    final_anonymized = anonym_dir / "video_hash.mp4"
    assert not final_anonymized.exists()
    assert video.processed_video_hash is None
    assert video.processed_file.name is None
    assert sensitive_working_copy.exists()
    assert history_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path_case", "message"),
    [
        ("none", "without anonymized output"),
        ("missing", "anonymized output is missing"),
    ],
)
def test_finalize_video_success_rejects_missing_anonymized_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    path_case: str,
    message: str,
) -> None:
    import endoreg_db.import_files.file_storage.state_management as state_management_module

    import_file = tmp_path / "import" / "exam.mp4"
    import_file.parent.mkdir(parents=True, exist_ok=True)
    import_file.write_bytes(b"import")

    history_calls: list[dict[str, object]] = []

    class DummyState:
        processing_started = False
        anonymized = False
        sensitive_meta_processed = False
        saved = False

        def mark_processing_started(self) -> None:
            self.processing_started = True

        def mark_anonymized(self) -> None:
            self.anonymized = True

        def mark_sensitive_meta_processed(self) -> None:
            self.sensitive_meta_processed = True

        def save(self) -> None:
            self.saved = True

    class DummyVideo:
        def __init__(self) -> None:
            self.pk = 1
            self.video_hash = "video_hash"
            self.processed_video_hash = None
            self.processed_file = SimpleNamespace(name=None)
            self.state = DummyState()
            self.saved = False

        def get_raw_file_path(self) -> Path:
            return tmp_path / "sensitive" / "video_hash.mp4"

        def save(self) -> None:
            self.saved = True

        def get_or_create_state(self) -> object:
            return self.state

    monkeypatch.setattr(
        state_management_module,
        "VideoFile",
        DummyVideo,
        raising=True,
    )

    def fake_processed_video_dir() -> Path:
        return tmp_path / "anonymized_videos"

    def fake_get_or_create_for_hash(**kwargs: object) -> None:
        history_calls.append(kwargs)

    monkeypatch.setattr(
        state_management_module,
        "_processed_video_dir",
        fake_processed_video_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(fake_get_or_create_for_hash),
        raising=True,
    )

    video = DummyVideo()
    ctx = ImportContext(
        file_path=import_file,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.file_hash = "file-hash"
    ctx.current_video = cast(VideoFile, video)
    ctx.anonymized_path = (
        None if path_case == "none" else tmp_path / "processing" / "missing.mp4"
    )

    with pytest.raises(RuntimeError, match=message):
        finalize_video_success(ctx)

    assert video.processed_file.name is None
    assert video.processed_video_hash is None
    assert video.saved is False
    assert video.state.processing_started is False
    assert video.state.anonymized is False
    assert video.state.sensitive_meta_processed is False
    assert video.state.saved is False
    assert history_calls == []
