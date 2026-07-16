import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
import json
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, UploadJob
from lx_dtypes.models.contracts.video_correction import VideoCorrectionSegmentUpdateData


def _load_video_view_module(module_name: str) -> Any:
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
def _context_path(path: Path) -> Any:
    yield path


def _patch_anonym_video_dir(
    module: Any, monkeypatch: pytest.MonkeyPatch, output_dir: Path
) -> None:
    def _from_environment(cls: Any) -> SimpleNamespace:
        return SimpleNamespace(anonym_video=output_dir, transcoding=output_dir)

    monkeypatch.setattr(
        module.path_utils.EndoregPathsModel,
        "from_environment",
        classmethod(_from_environment),
        raising=True,
    )

    def _masked_output_path(video: Any) -> Path:
        return output_dir / f"{video.video_hash}_masked.mp4"

    monkeypatch.setattr(
        module,
        "_masked_output_path",
        _masked_output_path,
        raising=True,
    )

    def _cleaned_output_path(video: Any) -> Path:
        return output_dir / f"{video.video_hash}_cleaned.mp4"

    monkeypatch.setattr(
        module,
        "_cleaned_output_path",
        _cleaned_output_path,
        raising=True,
    )


def _patch_processed_file_save(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def _save_processed(video: Any, output_path: Path) -> str:
        video.processed_file.name = f"processed_videos_final/{output_path.name}"
        return video.processed_file.name

    monkeypatch.setattr(
        module,
        "update_processed_file",
        _save_processed,
        raising=True,
    )


def _fake_get_object_or_404(video: "_FakeVideo") -> object:
    def _get_object_or_404(model: object, **kwargs: object) -> _FakeVideo:
        return video

    return _get_object_or_404


def _empty_segment_update_result(
    video: object,
    removed_frames: object,
) -> VideoCorrectionSegmentUpdateData:
    return {
        "segments_updated": 0,
        "segments_deleted": 0,
        "segments_unchanged": 0,
    }


class _FakeHistory:
    running: bool
    success: dict[str, Any] | None
    failure: Any

    def __init__(self) -> None:
        self.running = False
        self.success = None
        self.failure = None

    def mark_running(self) -> None:
        self.running = True

    def mark_success(self, **kwargs: Any) -> None:
        self.success = kwargs

    def mark_failure(self, error: Any) -> None:
        self.failure = error


class _FakeHistoryModel:
    OPERATION_MASKING: str = "masking"
    OPERATION_FRAME_REMOVAL: str = "frame_removal"
    STATUS_PENDING: str = "pending"

    class objects:
        @staticmethod
        def create(**kwargs: Any) -> _FakeHistory:
            return _FakeHistory()


class _FakeVideo:
    id: int
    pk: int
    video_hash: str
    raw_file: SimpleNamespace
    center: Any
    center_id: Any
    video_meta: SimpleNamespace
    sensitive_meta: Any
    sensitive_meta_id: Any
    frame_count: int
    processed_file: SimpleNamespace
    _raw_path: Path
    saved_update_fields: list[Any]
    refreshed: bool
    legacy_prediction_called: bool
    initialize_specs_called: bool
    initialize_frames_called: bool

    def __init__(self, raw_path: Path) -> None:
        self.id = 1
        self.pk = 1
        self.video_hash = "video-hash"
        self.raw_file = SimpleNamespace(name=raw_path.name, path=str(raw_path))
        self.center = SimpleNamespace(name="university_hospital_wuerzburg")
        self.center_id = None
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
        self.saved_update_fields = []
        self.refreshed = False
        self.legacy_prediction_called = False
        self.initialize_specs_called = False
        self.initialize_frames_called = False

    def get_raw_file_path(self) -> Path:
        return self._raw_path

    def initialize_video_specs(self) -> None:
        self.initialize_specs_called = True

    def initialize_frames(self) -> None:
        self.initialize_frames_called = True

    def save(self, update_fields: Any = None) -> None:
        self.saved_update_fields.append(update_fields)

    def refresh_from_db(self) -> None:
        self.refreshed = True

    def get_processed_file_path(self) -> str:
        return self.processed_file.name


def test_anonymization_correction_payload_requires_human_review() -> None:
    module: Any = _load_video_view_module("correction")

    with pytest.raises(ValueError, match="human_review_required"):
        module.VideoAnonymizationCorrectionView._validate_payload(
            {"strategy": "detector_assisted"}
        )


def test_anonymization_correction_accepts_detector_all_frame_strategy() -> None:
    module: Any = _load_video_view_module("correction")

    payload = module.VideoAnonymizationCorrectionView._validate_payload(
        {
            "strategy": "detector_assisted",
            "processing_method": "streaming",
            "human_review_required": True,
        }
    )

    assert payload["strategy"] == "detector_assisted"
    assert payload["apply_all_frames"] is True
    assert payload["human_review_required"] is True


def test_anonymization_correction_normalizes_custom_processor_region() -> None:
    module: Any = _load_video_view_module("correction")

    payload = module.VideoAnonymizationCorrectionView._validate_payload(
        {
            "strategy": "processor_region",
            "processing_method": "direct",
            "human_review_required": True,
            "region": {
                "mode": "custom",
                "roi": {"x": 10, "y": 20, "width": 300, "height": 200},
            },
        }
    )

    assert payload["region"] == {
        "mode": "custom",
        "device_name": "olympus_cv_1500",
        "roi": {"x": 10, "y": 20, "width": 300, "height": 200},
    }


def test_anonymization_correction_uses_public_all_frame_detector_method(
    tmp_path: Path,
) -> None:
    module: Any = _load_video_view_module("correction")
    calls: list[tuple[Path, Path]] = []

    class _Summary:
        @staticmethod
        def to_dict() -> dict[str, object]:
            return {"frames_processed": 12, "redactions_applied": 7}

    class _Cleaner:
        @staticmethod
        def mask_video_with_phi_detector(
            *, input_video: Path, output_video: Path
        ) -> _Summary:
            calls.append((input_video, output_video))
            return _Summary()

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    result = module.VideoAnonymizationCorrectionView._apply_strategy(
        frame_cleaner=_Cleaner(),
        raw_path=source,
        output_path=output,
        payload={"strategy": "detector_assisted"},
    )

    assert calls == [(source, output)]
    assert result == {"frames_processed": 12, "redactions_applied": 7}


@pytest.mark.django_db
def test_reimport_returns_clear_error_when_raw_source_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: Any = _load_video_view_module("reimport")
    import endoreg_db.services.video_reimport_orchestrator as reimport_orchestrator

    factory = APIRequestFactory()

    missing_raw: Path = tmp_path / "missing.mp4"
    video = _FakeVideo(missing_raw)

    class _FakeVideoModel:
        DoesNotExist = LookupError

        class objects:
            @staticmethod
            def select_related(*args: Any) -> Any:
                class _Inner:
                    @staticmethod
                    def get(**kwargs: Any) -> _FakeVideo:
                        return video

                return _Inner

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)

    def _ensure_local_file_fail(field_file: Any) -> Any:
        raise FileNotFoundError("raw source missing from storage")

    monkeypatch.setattr(
        reimport_orchestrator,
        "ensure_local_file",
        _ensure_local_file_fail,
        raising=True,
    )

    response: Any = module.VideoReimportView.as_view()(factory.post("/reimport/"), pk=1)
    data = json.loads(response.content)
    assert response.status_code == 404
    assert "could not be materialized from storage" in data["error"]


@pytest.mark.django_db
def test_reimport_reanonymizes_existing_video_without_full_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: Any = _load_video_view_module("reimport")
    import endoreg_db.services.jobs.video_reimport_jobs as reimport_jobs
    import endoreg_db.services.video_reimport_orchestrator as reimport_orchestrator

    factory = APIRequestFactory()

    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    service_calls: list[dict[str, Any]] = []
    hls_calls: list[Any] = []
    prediction_calls: list[tuple[Any, Any]] = []

    class _FakeVideoModel:
        DoesNotExist = LookupError

        class objects:
            @staticmethod
            def select_related(*args: Any) -> Any:
                class _Inner:
                    @staticmethod
                    def get(**kwargs: Any) -> _FakeVideo:
                        return video

                return _Inner

    class _FakeSensitiveMetaModel:
        class objects:
            @staticmethod
            def filter(**kwargs: Any) -> Any:
                return SimpleNamespace(delete=lambda: None)

    @contextmanager
    def _fake_atomic() -> Any:
        yield

    class _FakeService:
        def reanonymize_existing_video(
            self, target_video: Any, *, source_path: Any = None
        ) -> _FakeVideo:
            service_calls.append(
                {"target_video": target_video, "source_path": source_path}
            )
            return video

        def import_and_anonymize(self, **kwargs: Any) -> None:
            raise AssertionError("reimport should not use the full import pipeline")

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(
        reimport_orchestrator,
        "SensitiveMeta",
        _FakeSensitiveMetaModel,
        raising=True,
    )
    monkeypatch.setattr(
        reimport_orchestrator.transaction, "atomic", _fake_atomic, raising=True
    )

    def _ensure_local_file_mock(field_file: Any) -> Any:
        return _context_path(raw_path)

    monkeypatch.setattr(
        reimport_orchestrator,
        "ensure_local_file",
        _ensure_local_file_mock,
        raising=True,
    )

    def _dispatch_prediction_mock(target_video: Any, payload: Any) -> dict[str, Any]:
        prediction_calls.append((target_video, payload))
        return {"status": "queued", "queued": True, "history_id": 123}

    monkeypatch.setattr(
        reimport_orchestrator,
        "_dispatch_prediction_refresh",
        _dispatch_prediction_mock,
        raising=True,
    )

    def _regenerate_hls_mock(target_video: Any) -> dict[str, Any]:
        hls_calls.append(target_video)
        return {"status": "materialized", "key_id": "reimport-hls-key"}

    monkeypatch.setattr(
        reimport_orchestrator,
        "_regenerate_reimport_hls_artifacts",
        _regenerate_hls_mock,
        raising=True,
    )

    def _init_specs_mock(target_video: Any) -> None:
        target_video.initialize_video_specs()

    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_file_specs",
        _init_specs_mock,
        raising=True,
    )

    def _init_frames_mock(target_video: Any) -> None:
        target_video.initialize_frames()

    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_frames",
        _init_frames_mock,
        raising=True,
    )

    view: Any = module.VideoReimportView()
    view.video_service = _FakeService()

    response: Any = view.post(factory.post("/reimport/"), pk=1)
    data = response.data

    assert response.status_code == 200
    assert service_calls == [{"target_video": video, "source_path": raw_path}]
    assert video.legacy_prediction_called is False
    assert video.initialize_specs_called is True
    assert video.initialize_frames_called is True
    assert video.refreshed is True
    assert hls_calls == [video]
    assert prediction_calls == [(video, {})]
    assert data["prediction_refresh"]["queued"] is True


@pytest.mark.django_db
def test_reset_reimport_state_does_not_reactivate_duplicate_upload_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as reimport_jobs

    center: Any = Center.objects.create(name="reimport-center")
    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    video.center = center
    video.center_id = center.pk
    video.video_hash = "duplicate-video-hash"

    active_job: Any = UploadJob.objects.create(
        file=SimpleUploadedFile("active.mp4", b"active", content_type="video/mp4"),
        status=UploadJob.Status.ANONYMIZED,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )
    failed_job: Any = UploadJob.objects.create(
        file=SimpleUploadedFile("failed.mp4", b"failed", content_type="video/mp4"),
        status=UploadJob.Status.ERROR,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )

    def _init_specs_mock(target_video: Any) -> None:
        target_video.initialize_video_specs()

    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_file_specs",
        _init_specs_mock,
        raising=True,
    )

    def _init_frames_mock(target_video: Any) -> None:
        target_video.initialize_frames()

    monkeypatch.setattr(
        reimport_jobs,
        "initialize_video_frames",
        _init_frames_mock,
        raising=True,
    )

    reset_count: Any = getattr(reimport_jobs, "_reset_reimport_state")(cast(Any, video))

    active_job.refresh_from_db()
    failed_job.refresh_from_db()
    assert reset_count == 1
    assert active_job.status == UploadJob.Status.PROCESSING
    assert failed_job.status == UploadJob.Status.ERROR
    assert video.initialize_specs_called is True
    assert video.initialize_frames_called is True


@pytest.mark.django_db
def test_mark_upload_jobs_anonymized_leaves_duplicate_failed_jobs_inactive(
    tmp_path: Path,
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as reimport_jobs

    center: Any = Center.objects.create(name="reimport-complete-center")
    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    video.center = center
    video.center_id = center.pk
    video.video_hash = "complete-duplicate-video-hash"

    active_job: Any = UploadJob.objects.create(
        file=SimpleUploadedFile("active.mp4", b"active", content_type="video/mp4"),
        status=UploadJob.Status.PROCESSING,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )
    failed_job: Any = UploadJob.objects.create(
        file=SimpleUploadedFile("failed.mp4", b"failed", content_type="video/mp4"),
        status=UploadJob.Status.ERROR,
        content_type="video/mp4",
        source_center=center,
        content_hash=video.video_hash,
    )

    completed_count: Any = getattr(reimport_jobs, "_mark_upload_jobs_anonymized")(
        cast(Any, video)
    )

    active_job.refresh_from_db()
    failed_job.refresh_from_db()
    assert completed_count == 1
    assert active_job.status == UploadJob.Status.ANONYMIZED
    assert failed_job.status == UploadJob.Status.ERROR


@pytest.mark.django_db
def test_mask_replace_denied_cleans_part_and_preserves_processed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: Any = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir: Path = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeMaskApplication:
        def __init__(self) -> None:
            self.device_name = None

        def _load_mask(self) -> dict[str, str]:
            return {"mask": "ok"}

        def mask_video_streaming(self, *, output_video: Any, **kwargs: Any) -> bool:
            output_video.write_bytes(b"new-mask")
            return True

    class _FakeFrameCleaner:
        def __init__(self) -> None:
            self.mask_application = _FakeMaskApplication()

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
    monkeypatch.setattr(
        module, "get_object_or_404", _fake_get_object_or_404(video), raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )

    def _create_history(**kwargs: Any) -> _FakeHistory:
        return history

    monkeypatch.setattr(
        module.VideoProcessingHistory.objects, "create", _create_history, raising=False
    )

    original_name = video.processed_file.name
    final_output: Path = output_dir / f"{video.video_hash}_masked.mp4"
    stale_part: Path = output_dir / f"{video.video_hash}_masked.part.mp4"
    stale_part.write_bytes(b"stale")

    def _replace_fail(*, source: Any, destination: Any) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr(module, "atomic_move_file", _replace_fail, raising=True)

    response: Any = module.VideoApplyMaskView.as_view()(
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
def test_mask_overwrites_stale_part_and_updates_processed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: Any = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir: Path = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeMaskApplication:
        def __init__(self) -> None:
            self.device_name = None

        def _load_mask(self) -> dict[str, str]:
            return {"mask": "ok"}

        def mask_video_streaming(self, *, output_video: Any, **kwargs: Any) -> bool:
            output_video.write_bytes(b"fresh-mask")
            return True

    class _FakeFrameCleaner:
        def __init__(self) -> None:
            self.mask_application = _FakeMaskApplication()

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
    monkeypatch.setattr(
        module, "get_object_or_404", _fake_get_object_or_404(video), raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )

    def _create_history_success(**kwargs: Any) -> _FakeHistory:
        return history

    monkeypatch.setattr(
        module.VideoProcessingHistory.objects,
        "create",
        _create_history_success,
        raising=False,
    )
    _patch_processed_file_save(module, monkeypatch)

    stale_part: Path = output_dir / f"{video.video_hash}_masked.part.mp4"
    stale_part.write_bytes(b"stale")

    response: Any = module.VideoApplyMaskView.as_view()(
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

    final_output: Path = output_dir / f"{video.video_hash}_masked.mp4"

    assert response.status_code == 200
    assert final_output.exists()
    assert final_output.read_bytes() == b"fresh-mask"
    assert not stale_part.exists()
    assert video.processed_file.name.endswith(f"{video.video_hash}_masked.mp4")
    assert history.success is not None


@pytest.mark.django_db
def test_remove_frames_replace_denied_keeps_existing_processed_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: Any = _load_video_view_module("correction")
    factory = APIRequestFactory()

    raw_path: Path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    video = _FakeVideo(raw_path)
    history = _FakeHistory()
    output_dir: Path = tmp_path / "processed_videos_final"
    output_dir.mkdir(parents=True, exist_ok=True)

    class _FakeFrameCleaner:
        def remove_frames_from_video_streaming(
            self, *, output_video: Any, **kwargs: Any
        ) -> bool:
            output_video.write_bytes(b"cleaned")
            return True

    _patch_anonym_video_dir(module, monkeypatch, output_dir)
    monkeypatch.setattr(
        module, "get_object_or_404", _fake_get_object_or_404(video), raising=True
    )
    monkeypatch.setattr(module, "FrameCleaner", _FakeFrameCleaner, raising=True)
    monkeypatch.setattr(
        module, "VideoProcessingHistory", _FakeHistoryModel, raising=True
    )

    def _create_history_rf(**kwargs: Any) -> _FakeHistory:
        return history

    monkeypatch.setattr(
        module.VideoProcessingHistory.objects,
        "create",
        _create_history_rf,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "update_segments_after_frame_removal",
        _empty_segment_update_result,
        raising=True,
    )

    def _atomic_move_fail(*, source: Any, destination: Any) -> None:
        raise PermissionError("replace denied")

    monkeypatch.setattr(
        module,
        "atomic_move_file",
        _atomic_move_fail,
        raising=True,
    )

    original_name = video.processed_file.name

    response: Any = module.VideoRemoveFramesView.as_view()(
        factory.post(
            "/remove-frames/",
            {"frame_list": [1, 2], "processing_method": "direct"},
            format="json",
        ),
        pk=1,
    )

    part_path: Path = output_dir / f"{video.video_hash}_cleaned.part.mp4"
    final_output: Path = output_dir / f"{video.video_hash}_cleaned.mp4"

    assert response.status_code == 500
    assert video.processed_file.name == original_name
    assert not part_path.exists()
    assert not final_output.exists()
    assert history.failure is not None
