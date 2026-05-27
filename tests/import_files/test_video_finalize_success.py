from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.state_management import finalize_video_success
from endoreg_db.utils.filesystem import paths as paths_module


def _runtime_storage_root():
    return paths_module.EndoregPathsModel.from_environment().storage


@pytest.mark.unit
def test_finalize_video_success_keeps_only_canonical_raw_and_anonymized(
    tmp_path, monkeypatch
):
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

        def mark_processing_started(self):
            self.processing_started = True

        def mark_anonymized(self):
            self.anonymized = True

        def mark_sensitive_meta_processed(self):
            self.sensitive_meta_processed = True

        def save(self):
            return None

    class DummyVideo:
        def __init__(self):
            self.pk = 1
            self.video_hash = "video_hash"
            self.processed_file = SimpleNamespace(name=None)
            self.state = DummyState()

        def get_raw_file_path(self):
            return raw_path

        def save(self):
            return None

        def get_or_create_state(self):
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
    monkeypatch.setattr(
        state_management_module,
        "_processed_video_dir",
        lambda: anonym_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "nuke_transcoding_dir",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("finalize_video_success must not nuke global transcoding")
        ),
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(lambda **kwargs: None),
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "get_stream_info",
        lambda path: {"streams": [{"codec_type": "video"}]},
        raising=True,
    )
    monkeypatch.setattr(
        cleanup_module,
        "staging_cleanup_roots",
        lambda: (sensitive_dir,),
        raising=True,
    )

    video = DummyVideo()
    ctx = ImportContext(
        file_path=import_file,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.file_hash = "file-hash"
    ctx.current_video = video
    ctx.sensitive_path = sensitive_working_copy
    ctx.anonymized_path = temp_anonymized

    finalize_video_success(ctx)

    final_anonymized = anonym_dir / "video_hash.mp4"
    assert raw_path.exists()
    assert final_anonymized.exists()
    assert not sensitive_working_copy.exists()
    assert video.processed_file.name.endswith("anonymized_videos/video_hash.mp4")


@pytest.mark.unit
def test_finalize_video_success_rejects_unprobeable_final_output(tmp_path, monkeypatch):
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

    history_calls = []

    class DummyState:
        processing_started = False

        def mark_processing_started(self):
            self.processing_started = True

        def mark_anonymized(self):
            self.anonymized = True

        def mark_sensitive_meta_processed(self):
            self.sensitive_meta_processed = True

        def save(self):
            return None

    class DummyVideo:
        def __init__(self):
            self.pk = 1
            self.video_hash = "video_hash"
            self.processed_file = SimpleNamespace(name=None)
            self.state = DummyState()

        def get_raw_file_path(self):
            return raw_path

        def save(self):
            return None

        def get_or_create_state(self):
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
    monkeypatch.setattr(
        state_management_module,
        "_processed_video_dir",
        lambda: anonym_dir,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "nuke_transcoding_dir",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("finalize_video_success must not nuke global transcoding")
        ),
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.transaction,
        "atomic",
        fake_atomic,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module,
        "get_stream_info",
        lambda path: None,
        raising=True,
    )
    monkeypatch.setattr(
        state_management_module.ProcessingHistory,
        "get_or_create_for_hash",
        staticmethod(lambda **kwargs: history_calls.append(kwargs)),
        raising=True,
    )

    video = DummyVideo()
    ctx = ImportContext(
        file_path=import_file,
        center_name="university_hospital_wuerzburg",
        processor_name="olympus_cv_1500",
    )
    ctx.file_hash = "file-hash"
    ctx.current_video = video
    ctx.sensitive_path = sensitive_working_copy
    ctx.anonymized_path = temp_anonymized

    with pytest.raises(RuntimeError, match="ffprobe validation"):
        finalize_video_success(ctx)

    final_anonymized = anonym_dir / "video_hash.mp4"
    assert final_anonymized.exists()
    assert video.processed_file.name is None
    assert sensitive_working_copy.exists()
    assert history_calls == []
