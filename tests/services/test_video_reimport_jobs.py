# pyright: reportPrivateUsage=false
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from endoreg_db.config.env import EnvironmentValueError


def test_video_reimport_job_mode_rejects_unsupported_value(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as module

    raw_value = "sensitive-invalid-video-reimport-mode"
    monkeypatch.setenv("VIDEO_REIMPORT_JOB_MODE", raw_value)

    with pytest.raises(EnvironmentValueError) as error:
        module.get_video_reimport_job_mode()

    assert error.value.key == "VIDEO_REIMPORT_JOB_MODE"
    assert raw_value not in str(error.value)


def test_video_reimport_dispatch_delay_rejects_negative_value(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as module

    monkeypatch.setenv("VIDEO_REIMPORT_DISPATCH_DELAY_SECONDS", "-1")

    with pytest.raises(EnvironmentValueError) as error:
        module.get_video_reimport_dispatch_delay_seconds()

    assert error.value.key == "VIDEO_REIMPORT_DISPATCH_DELAY_SECONDS"


@contextmanager
def _context_path(path: Path) -> Any:
    yield path


@pytest.mark.parametrize("prediction_status", ["skipped", "not_queued", "failed"])
def test_async_reimport_uses_in_place_reanonymization(
    monkeypatch: MonkeyPatch, tmp_path: Path, prediction_status: str
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as module

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    events: list[tuple[str, object]] = []
    service_calls: list[dict[str, object]] = []
    hls_calls: list[object] = []

    class _FakeVideo:
        pk = 1
        raw_video_hash = "video-hash"
        raw_file = SimpleNamespace(name="raw.mp4")
        center = SimpleNamespace(name="university_hospital_wuerzburg")
        processor = SimpleNamespace(name="olympus_cv_1500")
        video_meta = SimpleNamespace(processor=processor)
        processed_file = SimpleNamespace(name="processed_videos_final/video.mp4")

        def refresh_from_db(self) -> None:
            events.append(("refresh_from_db", self))

    video = _FakeVideo()

    class _FakeVideoManager:
        def select_related(self, *args: str) -> "_FakeVideoManager":
            events.append(("select_related", args))
            return self

        def get(self, pk: int) -> _FakeVideo:
            assert pk == 1
            return video

    class _FakeVideoModel:
        objects = _FakeVideoManager()

    class _FakeService:
        def reanonymize_existing_video(
            self, target_video: object, *, source_path: Path | None = None
        ) -> object:
            service_calls.append(
                {"target_video": target_video, "source_path": source_path}
            )
            return target_video

        def import_and_anonymize(self, **kwargs: Any) -> object:
            raise AssertionError("async reimport should not use full import")

    @contextmanager
    def _fake_atomic() -> Any:
        yield

    def fake_ensure_local_file(field_file: object) -> Any:
        return _context_path(raw_path)

    def fake_reset_reimport_state(target_video: object) -> int:
        events.append(("reset", target_video))
        return 1

    def fake_mark_upload_jobs_anonymized(target_video: object) -> int:
        events.append(("mark_anonymized", target_video))
        return 1

    def fake_run_prediction_refresh(
        *, video: object, config: object
    ) -> dict[str, object]:
        return {"status": prediction_status, "queued": False}

    def fake_regenerate_hls(target_video: object) -> dict[str, object]:
        hls_calls.append(target_video)
        return {"status": "materialized", "key_id": "reimport-hls-key"}

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(
        module, "ensure_local_file", fake_ensure_local_file, raising=True
    )
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(
        module, "_reset_reimport_state", fake_reset_reimport_state, raising=True
    )
    monkeypatch.setattr(
        module,
        "_mark_upload_jobs_anonymized",
        fake_mark_upload_jobs_anonymized,
        raising=True,
    )
    monkeypatch.setattr(
        module, "_run_prediction_refresh", fake_run_prediction_refresh, raising=True
    )
    monkeypatch.setattr(
        module,
        "_regenerate_reimport_hls_artifacts",
        fake_regenerate_hls,
        raising=True,
    )

    def fake_video_import_service_factory() -> _FakeService:
        return _FakeService()

    monkeypatch.setattr(module, "VideoImportService", fake_video_import_service_factory)

    assert module._run_video_reimport_job(1) is True  # pyright: ignore[reportPrivateUsage]

    assert ("reset", video) in events
    assert service_calls == [{"target_video": video, "source_path": raw_path}]
    assert hls_calls == [video]
    assert ("mark_anonymized", video) in events


def test_async_reimport_fails_if_hls_regeneration_fails(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.services.jobs.video_reimport_jobs as module

    raw_path = tmp_path / "raw.mp4"
    raw_path.write_bytes(b"raw")
    events: list[tuple[str, object, object | None]] = []

    class _FakeVideo:
        pk = 1
        raw_video_hash = "video-hash"
        raw_file = SimpleNamespace(name="raw.mp4")
        center = SimpleNamespace(name="university_hospital_wuerzburg")
        processor = SimpleNamespace(name="olympus_cv_1500")
        video_meta = SimpleNamespace(processor=processor)
        processed_file = SimpleNamespace(name="processed_videos_final/video.mp4")

        def refresh_from_db(self) -> None:
            events.append(("refresh_from_db", self, None))

    video = _FakeVideo()

    class _FakeVideoManager:
        def select_related(self, *args: str) -> "_FakeVideoManager":
            return self

        def get(self, pk: int) -> _FakeVideo:
            assert pk == 1
            return video

    class _FakeVideoModel:
        objects = _FakeVideoManager()

    class _FakeService:
        def reanonymize_existing_video(
            self, target_video: object, *, source_path: Path | None = None
        ) -> object:
            events.append(("reanonymize", target_video, source_path))
            return target_video

    @contextmanager
    def _fake_atomic() -> Any:
        yield

    def fake_ensure_local_file(field_file: object) -> Any:
        return _context_path(raw_path)

    def fake_reset_reimport_state(target_video: object) -> int:
        events.append(("reset", target_video, None))
        return 1

    def fake_mark_upload_jobs_error(target_video: object, error_detail: str) -> int:
        events.append(("mark_error", target_video, error_detail))
        return 1

    def fail_mark_upload_jobs_anonymized(target_video: object) -> int:
        raise AssertionError("failed HLS regeneration must not mark upload anonymized")

    def fail_prediction_refresh(*, video: object, config: object) -> dict[str, object]:
        raise AssertionError("failed HLS regeneration must not refresh predictions")

    def fail_regenerate_hls(target_video: object) -> dict[str, object]:
        raise RuntimeError("hls regeneration failed")

    monkeypatch.setattr(module, "VideoFile", _FakeVideoModel, raising=True)
    monkeypatch.setattr(
        module, "ensure_local_file", fake_ensure_local_file, raising=True
    )
    monkeypatch.setattr(module.transaction, "atomic", _fake_atomic, raising=True)
    monkeypatch.setattr(
        module, "_reset_reimport_state", fake_reset_reimport_state, raising=True
    )
    monkeypatch.setattr(
        module,
        "_mark_upload_jobs_error",
        fake_mark_upload_jobs_error,
        raising=True,
    )
    monkeypatch.setattr(
        module,
        "_mark_upload_jobs_anonymized",
        fail_mark_upload_jobs_anonymized,
        raising=True,
    )
    monkeypatch.setattr(
        module, "_run_prediction_refresh", fail_prediction_refresh, raising=True
    )
    monkeypatch.setattr(
        module,
        "_regenerate_reimport_hls_artifacts",
        fail_regenerate_hls,
        raising=True,
    )
    monkeypatch.setattr(module, "VideoImportService", lambda: _FakeService())

    with pytest.raises(RuntimeError, match="hls regeneration failed"):
        module._run_video_reimport_job(1)  # pyright: ignore[reportPrivateUsage]

    assert ("mark_error", video, "hls regeneration failed") in events


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ValueError("invalid prediction config"), "not_queued"),
        (RuntimeError("broker unavailable"), "failed"),
    ],
)
def test_prediction_failure_has_same_outcome_inline_and_worker(
    monkeypatch: MonkeyPatch, failure: Exception, expected: str
) -> None:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services.jobs import video_reimport_jobs as jobs
    from endoreg_db.services.video_reimport_orchestrator import (
        VideoReimportOrchestrator,
    )

    video = VideoFile(id=17, raw_video_hash="prediction-parity")

    def fail_dispatch(video: VideoFile, payload: object) -> dict[str, object]:
        raise failure

    monkeypatch.setattr(jobs, "_dispatch_prediction_refresh", fail_dispatch)
    config = jobs._config_from_payload({}, queue="pipeline")
    worker = jobs._run_prediction_refresh(video=video, config=config)
    inline = VideoReimportOrchestrator(
        video=video, video_id=17, payload={}
    )._maybe_dispatch_prediction_refresh()
    assert (
        inline == worker == {"status": expected, "queued": False, "error": str(failure)}
    )


@pytest.mark.parametrize(
    "hls_status",
    ["materialized", "already_ready", "ready", "queued", "materializing", "failed"],
)
def test_reimport_requires_ready_hls(monkeypatch: MonkeyPatch, hls_status: str) -> None:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.services import hls_media
    from endoreg_db.services.jobs import video_reimport_jobs as jobs

    result = hls_media.HlsMaterializationResult(
        video_id=17,
        artifact_kind="processed",
        status=hls_status,
        key_id="",
        playlist_relative_path="",
        segment_directory_relative_path="",
        segment_count=0,
    )

    def materialize(
        *args: object, **kwargs: object
    ) -> hls_media.HlsMaterializationResult:
        return result

    monkeypatch.setattr(hls_media, "materialize_video_hls", materialize)
    video = VideoFile(id=17)
    if hls_status in {"materialized", "already_ready"}:
        assert jobs._regenerate_reimport_hls_artifacts(video)["status"] == hls_status
    else:
        with pytest.raises(RuntimeError, match="HLS is not ready"):
            jobs._regenerate_reimport_hls_artifacts(video)


@pytest.mark.parametrize("wrapped", [False, True])
def test_reimport_timeout_persists_safe_failure_without_hls_or_retry(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    wrapped: bool,
) -> None:
    from unittest.mock import Mock
    from billiard.exceptions import SoftTimeLimitExceeded
    import endoreg_db.services.jobs.video_reimport_jobs as jobs

    video = Mock()
    video.raw_file = "raw.mp4"
    history = Mock(status="running")
    error = SoftTimeLimitExceeded()
    failure = RuntimeError("wrapped vendor error")
    failure.__cause__ = error
    service = Mock()
    service.reanonymize_existing_video.side_effect = failure if wrapped else error
    manager = Mock()
    manager.select_related.return_value.get.return_value = video
    monkeypatch.setattr(jobs.VideoFile, "objects", manager)
    monkeypatch.setattr(jobs, "_get_processing_history", Mock(return_value=history))
    monkeypatch.setattr(jobs, "defer_if_video_media_busy", Mock())
    monkeypatch.setattr(jobs, "_config_from_history", Mock(return_value=object()))

    def source_context(_: object) -> Any:
        return _context_path(tmp_path / "raw.mp4")

    monkeypatch.setattr(jobs, "ensure_local_file", source_context)
    monkeypatch.setattr(jobs.transaction, "atomic", lambda: _context_path(tmp_path))
    monkeypatch.setattr(jobs, "_reset_reimport_state", Mock(return_value=0))
    monkeypatch.setattr(jobs, "VideoImportService", Mock(return_value=service))
    upload_failure = Mock()
    history_failure = Mock()
    publish = Mock()
    monkeypatch.setattr(jobs, "_mark_upload_jobs_error", upload_failure)
    monkeypatch.setattr(jobs, "_mark_history_failure", history_failure)
    monkeypatch.setattr(jobs, "_regenerate_reimport_hls_artifacts", publish)
    with pytest.raises((SoftTimeLimitExceeded, RuntimeError)):
        jobs._run_video_reimport_job(15)
    upload_failure.assert_called_once_with(video, "processing_timeout")
    history_failure.assert_called_once_with(history, "processing_timeout")
    publish.assert_not_called()
    history.mark_success.assert_not_called()


@pytest.mark.parametrize(
    "frame_count,expected_status",
    [(105307, "queued"), (None, "failed"), (1000000, "failed")],
)
def test_dispatch_supplies_bounded_limits_or_rejects(
    monkeypatch: MonkeyPatch,
    frame_count: int | None,
    expected_status: str,
) -> None:
    from unittest.mock import Mock
    import endoreg_db.services.jobs.video_reimport_jobs as jobs
    from endoreg_db.tasks import run_video_reimport_task

    video = Mock(pk=15, frame_count=frame_count)
    history = Mock(pk=1)
    manager = Mock()
    manager.get.return_value = video
    monkeypatch.setattr(jobs.VideoFile, "objects", manager)
    monkeypatch.setattr(
        jobs, "get_video_reimport_job_mode", Mock(return_value="celery")
    )
    monkeypatch.setattr(
        jobs, "_reserve_reimport_history", Mock(return_value=(history, "new"))
    )
    monkeypatch.setattr(jobs, "ensure_secure_transport_for_job_kind", Mock())
    monkeypatch.setattr(jobs, "_set_history_task_id", Mock())
    enqueue = Mock(return_value=SimpleNamespace(id="opaque-test-task"))
    monkeypatch.setattr(run_video_reimport_task, "apply_async", enqueue)
    result = jobs.dispatch_video_reimport(video_id=15)
    assert result.status == expected_status
    if expected_status == "queued":
        assert enqueue.call_args.kwargs["soft_time_limit"] == 35193
        assert enqueue.call_args.kwargs["time_limit"] == 35493
    else:
        enqueue.assert_not_called()
        history.mark_failure.assert_called_once()
