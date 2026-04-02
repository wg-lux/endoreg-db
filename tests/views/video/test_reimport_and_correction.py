import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rest_framework.test import APIRequestFactory


def _load_video_view_module(module_name: str):
    """Load a single video view module without importing the package __init__."""
    base = Path(__file__).resolve().parents[3] / "endoreg_db" / "views"

    views_pkg = types.ModuleType("endoreg_db.views")
    views_pkg.__path__ = [str(base)]
    sys.modules.setdefault("endoreg_db.views", views_pkg)

    video_pkg = types.ModuleType("endoreg_db.views.video")
    video_pkg.__path__ = [str(base / "video")]
    sys.modules.setdefault("endoreg_db.views.video", video_pkg)

    full_name = f"endoreg_db.views.video.{module_name}"
    module_path = base / "video" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextmanager
def _context_path(path: Path):
    yield path


class _FakeHistory:
    def __init__(self):
        self.running = False
        self.success = None
        self.failure = None

    def mark_running(self):
        self.running = True

    def mark_success(self, **kwargs):
        self.success = kwargs

    def mark_failure(self, error):
        self.failure = error


class _FakeHistoryModel:
    OPERATION_MASKING = "masking"
    OPERATION_FRAME_REMOVAL = "frame_removal"
    STATUS_PENDING = "pending"

    class objects:
        @staticmethod
        def create(**kwargs):
            return _FakeHistory()


class _FakeVideo:
    def __init__(self, raw_path: Path):
        self.id = 1
        self.pk = 1
        self.video_hash = "video-hash"
        self.raw_file = SimpleNamespace(name=raw_path.name, path=str(raw_path))
        self.center = SimpleNamespace(name="university_hospital_wuerzburg")
        self.video_meta = SimpleNamespace(
            processor=SimpleNamespace(name="olympus_cv_1500")
        )
        self.sensitive_meta = None
        self.sensitive_meta_id = None
        self.frame_count = 100
        self.processed_file = SimpleNamespace(
            name="processed_videos_final/original.mp4"
        )
        self._raw_path = raw_path
        self.saved_update_fields: list[Any] = []
        self.refreshed = False
        self.pipe_1_called = False
        self.initialize_specs_called = False
        self.initialize_frames_called = False

    def get_raw_file_path(self):
        return self._raw_path

    def initialize_video_specs(self):
        self.initialize_specs_called = True

    def initialize_frames(self):
        self.initialize_frames_called = True

    def pipe_1(self, **kwargs):
        self.pipe_1_called = True
        return True

    def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)

    def refresh_from_db(self):
        self.refreshed = True

    def get_processed_file_path(self):
        return self.processed_file.name


@pytest.mark.django_db
def test_reimport_returns_clear_error_when_raw_source_is_missing(tmp_path, monkeypatch):
    module = _load_video_view_module("reimport")
    factory = APIRequestFactory()

    missing_raw = tmp_path / "missing.mp4"
    video = _FakeVideo(missing_raw)

    class _FakeVideoModel:
        DoesNotExist = LookupError
        objects = SimpleNamespace(get=lambda **kwargs: video)

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(
        module,
        "ensure_local_file",
        lambda field_file: (_ for _ in ()).throw(
            FileNotFoundError("raw source missing from storage")
        ),
        raising=True,
    )

    response = module.VideoReimportView.as_view()(factory.post("/reimport/"), pk=1)

    assert response.status_code == 404
    assert "could not be materialized from storage" in response.data["error"]


@pytest.mark.django_db
def test_reimport_uses_retry_true_and_refreshes_video(tmp_path, monkeypatch):
    module = _load_video_view_module("reimport")
    factory = APIRequestFactory()

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    service_calls = []

    class _FakeVideoModel:
        DoesNotExist = LookupError
        objects = SimpleNamespace(get=lambda **kwargs: video)

    class _FakeSensitiveMetaModel:
        class objects:
            @staticmethod
            def filter(**kwargs):
                return SimpleNamespace(delete=lambda: None)

    @contextmanager
    def _fake_atomic():
        yield

    class _FakeService:
        def import_and_anonymize(self, **kwargs):
            service_calls.append(kwargs)
            return video

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(module, "SensitiveMeta", _FakeSensitiveMetaModel, raising=True)
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(
        module,
        "ensure_local_file",
        lambda field_file: _context_path(raw_path),
        raising=True,
    )

    view = module.VideoReimportView()
    view.video_service = _FakeService()

    response = view.post(factory.post("/reimport/"), pk=1)

    assert response.status_code == 200
    assert service_calls[0]["retry"] is True
    assert service_calls[0]["file_path"] == raw_path
    assert video.pipe_1_called is True
    assert video.refreshed is True


@pytest.mark.django_db
def test_mask_replace_denied_cleans_part_and_preserves_processed_path(
    tmp_path, monkeypatch
):
    module = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeMaskApplication:
        def __init__(self):
            self.device_name = None

        def _load_mask(self):
            return {"mask": "ok"}

        def mask_video_streaming(self, *, output_video, **kwargs):
            output_video.write_bytes(b"new-mask")
            return True

    class _FakeFrameCleaner:
        def __init__(self):
            self.mask_application = _FakeMaskApplication()

    monkeypatch.setattr(module, "ANONYM_VIDEO_DIR", output_dir, raising=True)
    monkeypatch.setattr(
        module, "get_object_or_404", lambda *args, **kwargs: video, raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )

    def _create_history(**kwargs):
        return history

    monkeypatch.setattr(
        module.VideoProcessingHistory.objects, "create", _create_history, raising=False
    )

    original_name = video.processed_file.name
    final_output = output_dir / f"{video.video_hash}_masked.mp4"
    stale_part = output_dir / f"{video.video_hash}_masked.part.mp4"
    stale_part.write_bytes(b"stale")

    def _replace_fail(src, dst):
        raise PermissionError("replace denied")

    monkeypatch.setattr(module.os, "replace", _replace_fail, raising=True)

    response = module.VideoApplyMaskView.as_view()(
        factory.post(
            "/apply-mask/",
            {
                "mask_type": "device",
                "device_name": "olympus_cv_1500",
                "processing_method": "direct",
            },
            format="json",
        ),
        pk=1,
    )

    assert response.status_code == 500
    assert video.processed_file.name == original_name
    assert not stale_part.exists()
    assert not final_output.exists()
    assert history.failure is not None


@pytest.mark.django_db
def test_mask_overwrites_stale_part_and_updates_processed_file(tmp_path, monkeypatch):
    module = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeMaskApplication:
        def __init__(self):
            self.device_name = None

        def _load_mask(self):
            return {"mask": "ok"}

        def mask_video_streaming(self, *, output_video, **kwargs):
            output_video.write_bytes(b"fresh-mask")
            return True

    class _FakeFrameCleaner:
        def __init__(self):
            self.mask_application = _FakeMaskApplication()

    monkeypatch.setattr(module, "ANONYM_VIDEO_DIR", output_dir, raising=True)
    monkeypatch.setattr(
        module, "get_object_or_404", lambda *args, **kwargs: video, raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )
    monkeypatch.setattr(
        module.VideoProcessingHistory.objects,
        "create",
        lambda **kwargs: history,
        raising=False,
    )

    stale_part = output_dir / f"{video.video_hash}_masked.part.mp4"
    stale_part.write_bytes(b"stale")

    response = module.VideoApplyMaskView.as_view()(
        factory.post(
            "/apply-mask/",
            {
                "mask_type": "device",
                "device_name": "olympus_cv_1500",
                "processing_method": "direct",
            },
            format="json",
        ),
        pk=1,
    )

    final_output = output_dir / f"{video.video_hash}_masked.mp4"

    assert response.status_code == 200
    assert final_output.exists()
    assert final_output.read_bytes() == b"fresh-mask"
    assert not stale_part.exists()
    assert video.processed_file.name.endswith(f"{video.video_hash}_masked.mp4")
    assert history.success is not None


@pytest.mark.django_db
def test_remove_frames_replace_denied_keeps_existing_processed_path(
    tmp_path, monkeypatch
):
    module = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeFrameCleaner:
        def remove_frames_from_video_streaming(self, *, output_video, **kwargs):
            output_video.write_bytes(b"cleaned")
            return True

    monkeypatch.setattr(module, "ANONYM_VIDEO_DIR", output_dir, raising=True)
    monkeypatch.setattr(
        module, "get_object_or_404", lambda *args, **kwargs: video, raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )
    monkeypatch.setattr(
        module.VideoProcessingHistory.objects,
        "create",
        lambda **kwargs: history,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "update_segments_after_frame_removal",
        lambda *args, **kwargs: {"segments_updated": 0, "segments_deleted": 0},
        raising=True,
    )
    monkeypatch.setattr(
        module.os,
        "replace",
        lambda src, dst: (_ for _ in ()).throw(PermissionError("replace denied")),
        raising=True,
    )

    original_name = video.processed_file.name

    response = module.VideoRemoveFramesView.as_view()(
        factory.post(
            "/remove-frames/",
            {"frame_list": [1, 2], "processing_method": "direct"},
            format="json",
        ),
        pk=1,
    )

    part_path = output_dir / f"{video.video_hash}_cleaned.part.mp4"
    final_output = output_dir / f"{video.video_hash}_cleaned.mp4"

    assert response.status_code == 500
    assert video.processed_file.name == original_name
    assert not part_path.exists()
    assert not final_output.exists()
    assert history.failure is not None
