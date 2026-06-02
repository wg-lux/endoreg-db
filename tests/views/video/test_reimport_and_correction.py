import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, UploadJob


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


def _patch_anonym_video_dir(module, monkeypatch, output_dir: Path):
    monkeypatch.setattr(
        module.path_utils.EndoregPathsModel,
        "from_environment",
        classmethod(
            lambda cls: SimpleNamespace(anonym_video=output_dir, transcoding=output_dir)
        ),
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_masked_output_path",
        lambda video: output_dir / f"{video.video_hash}_masked.mp4",
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_cleaned_output_path",
        lambda video: output_dir / f"{video.video_hash}_cleaned.mp4",
        raising=True,
    )


def _patch_processed_file_save(module, monkeypatch):
    def _save_processed(video, output_path: Path) -> str:
        video.processed_file.name = f"processed_videos_final/{output_path.name}"
        return video.processed_file.name

    monkeypatch.setattr(
        module,
        "update_processed_file",
        _save_processed,
        raising=True,
    )


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
        self.legacy_prediction_called = False
        self.initialize_specs_called = False
        self.initialize_frames_called = False

    def get_raw_file_path(self):
        return self._raw_path

    def initialize_video_specs(self):
        self.initialize_specs_called = True

    def initialize_frames(self):
        self.initialize_frames_called = True

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
        objects = SimpleNamespace(
            select_related=lambda *args: SimpleNamespace(get=lambda **kwargs: video)
        )

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
def test_reimport_reanonymizes_existing_video_without_full_import(
    tmp_path, monkeypatch
):
    module = _load_video_view_module("reimport")
    import endoreg_db.services.jobs.video_reimport_jobs as reimport_jobs

    factory = APIRequestFactory()

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    service_calls = []
    prediction_calls = []

    class _FakeVideoModel:
        DoesNotExist = LookupError
        objects = SimpleNamespace(
            select_related=lambda *args: SimpleNamespace(get=lambda **kwargs: video)
        )

    class _FakeSensitiveMetaModel:
        class objects:
            @staticmethod
            def filter(**kwargs):
                return SimpleNamespace(delete=lambda: None)

    @contextmanager
    def _fake_atomic():
        yield

    class _FakeService:
        def reanonymize_existing_video(self, target_video, *, source_path=None):
            service_calls.append(
                {"target_video": target_video, "source_path": source_path}
            )
            return video

        def import_and_anonymize(self, **kwargs):
            raise AssertionError("reimport should not use the full import pipeline")

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(module, "SensitiveMeta", _FakeSensitiveMetaModel, raising=True)
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(
        module,
        "ensure_local_file",
        lambda field_file: _context_path(raw_path),
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_dispatch_prediction_refresh",
        lambda target_video, payload: (
            prediction_calls.append((target_video, payload))
            or {"status": "queued", "queued": True, "history_id": 123}
        ),
        raising=True,
    )
    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_specs",
        lambda target_video: target_video.initialize_video_specs(),
        raising=True,
    )
    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_frames",
        lambda target_video: target_video.initialize_frames(),
        raising=True,
    )

    view = module.VideoReimportView()
    view.video_service = _FakeService()

    response = view.post(factory.post("/reimport/"), pk=1)

    assert response.status_code == 200
    assert service_calls == [{"target_video": video, "source_path": raw_path}]
    assert video.legacy_prediction_called is False
    assert video.initialize_specs_called is True
    assert video.initialize_frames_called is True
    assert video.refreshed is True
    assert prediction_calls == [(video, {})]
    assert response.data["prediction_refresh"]["queued"] is True


@pytest.mark.django_db
def test_reset_reimport_state_does_not_reactivate_duplicate_upload_jobs(
    tmp_path,
    monkeypatch,
):
    module = _load_video_view_module("reimport")
    import endoreg_db.services.jobs.video_reimport_jobs as reimport_jobs

    center = Center.objects.create(name="reimport-center")
    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    video.center = center
    video.center_id = center.pk
    video.video_hash = "duplicate-video-hash"

    active_job = UploadJob.objects.create(
        file=SimpleUploadedFile("active.mp4", b"active", content_type="video/mp4"),
        status=UploadJob.Status.ANONYMIZED,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )
    failed_job = UploadJob.objects.create(
        file=SimpleUploadedFile("failed.mp4", b"failed", content_type="video/mp4"),
        status=UploadJob.Status.ERROR,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )
    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_specs",
        lambda target_video: target_video.initialize_video_specs(),
        raising=True,
    )
    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_frames",
        lambda target_video: target_video.initialize_frames(),
        raising=True,
    )

    reset_count = module._reset_reimport_state(video)

    active_job.refresh_from_db()
    failed_job.refresh_from_db()
    assert reset_count == 1
    assert active_job.status == UploadJob.Status.PROCESSING
    assert failed_job.status == UploadJob.Status.ERROR
    assert video.initialize_specs_called is True
    assert video.initialize_frames_called is True


@pytest.mark.django_db
def test_mark_upload_jobs_anonymized_leaves_duplicate_failed_jobs_inactive(tmp_path):
    module = _load_video_view_module("reimport")
    center = Center.objects.create(name="reimport-complete-center")
    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    video.center = center
    video.center_id = center.pk
    video.video_hash = "complete-duplicate-video-hash"

    active_job = UploadJob.objects.create(
        file=SimpleUploadedFile("active.mp4", b"active", content_type="video/mp4"),
        status=UploadJob.Status.PROCESSING,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )
    failed_job = UploadJob.objects.create(
        file=SimpleUploadedFile("failed.mp4", b"failed", content_type="video/mp4"),
        status=UploadJob.Status.ERROR,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )

    completed_count = module._mark_upload_jobs_anonymized(video)

    active_job.refresh_from_db()
    failed_job.refresh_from_db()
    assert completed_count == 1
    assert active_job.status == UploadJob.Status.ANONYMIZED
    assert failed_job.status == UploadJob.Status.ERROR


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

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
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

    def _replace_fail(*, source, destination):
        raise PermissionError("replace denied")

    monkeypatch.setattr(module, "atomic_move_file", _replace_fail, raising=True)

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

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
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
    _patch_processed_file_save(module, monkeypatch)

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

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
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
        module,
        "atomic_move_file",
        lambda *, source, destination: (_ for _ in ()).throw(
            PermissionError("replace denied")
        ),
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
