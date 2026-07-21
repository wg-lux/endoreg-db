from __future__ import annotations

import types
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from types import TracebackType
from unittest.mock import Mock

import pytest
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    InformationSource,
    Label,
    VideoFile,
    VideoProcessingHistory,
)
from endoreg_db.models.state import video_segment_validation as segment_state
from endoreg_db.services import video_temporal_inference as temporal_jobs
from endoreg_db.services.jobs import video_post_validation_jobs as jobs
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    create_video_stream_lease,
)


def _create_video_for_post_validation(tmp_path: Path) -> VideoFile:
    center = Center.objects.create(
        name=f"post-validation-center-{uuid.uuid4().hex[:8]}",
        display_name="Post Validation Center",
    )
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"post-validation-{uuid.uuid4().hex}",
        frame_count=2,
        frame_dir=str(frame_dir),
    )
    video.processed_file.name = f"anonym_videos/{video.video_hash}.mp4"
    video.save(update_fields=["processed_file"])
    return video


class _FakeFuture:
    def done(self) -> bool:
        return True


class _ProcessedVideoContext:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __enter__(self) -> Path:
        self._path.write_bytes(b"video")
        return self._path

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


def _valid_video_stream_info(_path: Path) -> JsonObject:
    return {"streams": [{"codec_type": "video"}]}


def _audio_stream_info(_path: Path) -> JsonObject:
    return {"streams": [{"codec_type": "audio"}]}


def _blackening_config_json(*, only_validated: bool) -> JsonValue:
    config = segment_state.blackening_history_config(only_validated=only_validated)
    return {
        "kind": config["kind"],
        "only_validated": config["only_validated"],
        "queue": config["queue"],
    }


def _ensure_local_processed_context(
    video: VideoFile, tmp_path: Path
) -> _ProcessedVideoContext:
    assert video.pk is not None
    return _ProcessedVideoContext(tmp_path / "rebuilt.mp4")


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_inline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "inline")

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "inline"
    assert result.status == "completed"
    assert result.validation_status == "completed"
    assert result.video_id == video.pk
    assert result.history_id is not None
    runner.assert_called_once_with(
        video.pk,
        only_validated=False,
        history_id=result.history_id,
    )


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")

    submitted: dict[str, Callable[[], bool]] = {}

    def _fake_submit(fn: Callable[[], bool]) -> _FakeFuture:
        submitted["fn"] = fn
        return _FakeFuture()

    monkeypatch.setattr(jobs._executor, "submit", _fake_submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)
    assert result.mode == "thread"
    assert result.status == "queued"
    assert result.validation_status == "scheduled"
    assert result.video_id == video.pk
    assert result.history_id is not None
    assert "fn" in submitted

    # Execute the captured callable to verify background payload works.
    submitted["fn"]()
    runner.assert_called_once_with(
        video.pk,
        only_validated=False,
        history_id=result.history_id,
    )


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_celery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")

    fake_async_result = types.SimpleNamespace(id="celery-task-xyz")
    fake_task = types.SimpleNamespace(apply_async=Mock(return_value=fake_async_result))
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        fake_task,
        raising=False,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "celery"
    assert result.status == "queued"
    assert result.validation_status == "scheduled"
    assert result.task_id == "celery-task-xyz"
    assert result.video_id == video.pk
    assert result.history_id is not None
    fake_task.apply_async.assert_called_once_with(
        args=(video.pk,),
        kwargs={
            "only_validated": False,
            "history_id": result.history_id,
        },
        queue=jobs.get_celery_ffmpeg_media_queue(),
        routing_key=jobs.get_celery_ffmpeg_media_queue(),
        countdown=jobs.get_video_post_validation_dispatch_delay_seconds(),
    )
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.task_id == "celery-task-xyz"
    assert history.config["queue"] == jobs.get_celery_ffmpeg_media_queue()


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_celery_failure_does_not_fall_back_to_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    state = video.get_or_create_state()
    state.segment_annotations_created = True
    state.segment_annotations_validated = True
    state.outside_segments_removed = True
    state.save(
        update_fields=[
            "segment_annotations_created",
            "segment_annotations_validated",
            "outside_segments_removed",
            "date_modified",
        ]
    )
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")

    class _BrokenTask:
        def apply_async(
            self,
            *,
            args: tuple[int],
            kwargs: dict[str, int | bool],
            queue: str,
            routing_key: str,
            countdown: int,
        ) -> None:
            assert args == (video.pk,)
            assert kwargs["only_validated"] is False
            assert queue == routing_key
            assert countdown >= 0
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        _BrokenTask(),
        raising=False,
    )

    runner = Mock(side_effect=AssertionError("must not run in web process"))
    submit = Mock(side_effect=AssertionError("must not submit thread fallback"))
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setattr(jobs._executor, "submit", submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "celery"
    assert result.status == "failed"
    assert result.video_id == video.pk
    assert result.history_id is not None
    runner.assert_not_called()
    submit.assert_not_called()
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert "broker unavailable" in history.details
    state.refresh_from_db()
    assert state.segment_annotations_validated is False
    assert state.outside_segments_removed is False
    assert segment_state.resolve_segment_annotation_status(video) == "cleanup_failed"


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_celery_fails_without_required_secure_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")
    monkeypatch.setenv("CELERY_FFMPEG_MEDIA_REQUIRE_SECURE_TRANSPORT", "1")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker.local/0")
    monkeypatch.delenv("CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED", raising=False)

    fake_task = types.SimpleNamespace(
        apply_async=Mock(
            side_effect=AssertionError("insecure broker must not dispatch")
        )
    )
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        fake_task,
        raising=False,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "celery"
    assert result.status == "failed"
    fake_task.apply_async.assert_not_called()
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert "secure broker transport" in history.details


def test_blackening_history_config_schema_accepts_valid_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELERY_FFMPEG_MEDIA_QUEUE", "ffmpeg_media_hi")

    config = _blackening_config_json(only_validated=True)
    parsed = segment_state._parse_blackening_history_config(config)  # pyright: ignore[reportPrivateUsage]

    assert parsed is not None
    assert parsed.kind == segment_state.OUTSIDE_FRAME_BLACKENING_KIND
    assert parsed.only_validated is True
    assert parsed.queue == "ffmpeg_media_hi"


@pytest.mark.parametrize(
    "config",
    [
        {
            "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
            "only_validated": "yes",
        },
        {
            "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
            "only_validated": False,
            "queue": "",
        },
    ],
)
def test_blackening_history_config_schema_rejects_invalid_config(
    config: JsonValue,
) -> None:
    with pytest.raises(segment_state.OutsideFrameBlackeningConfigError):
        segment_state._parse_blackening_history_config(config)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("legacy_config", "expected_only_validated", "expected_queue"),
    [
        (
            {"kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND},
            False,
            "ffmpeg_media_hi",
        ),
        (
            {
                "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
                "only_validated": True,
            },
            True,
            "ffmpeg_media_hi",
        ),
        (
            {
                "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
                "queue": "legacy_ffmpeg_queue",
            },
            False,
            "legacy_ffmpeg_queue",
        ),
    ],
)
def test_blackening_history_repairs_recognized_legacy_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    legacy_config: dict[str, object],
    expected_only_validated: bool,
    expected_queue: str,
) -> None:
    monkeypatch.setenv("CELERY_FFMPEG_MEDIA_QUEUE", "ffmpeg_media_hi")
    video = _create_video_for_post_validation(tmp_path)
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=legacy_config,
    )

    assert segment_state.is_outside_frame_blackening_history(history) is True
    history.refresh_from_db()
    assert history.config == {
        "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
        "only_validated": expected_only_validated,
        "queue": expected_queue,
    }

    assert segment_state.is_outside_frame_blackening_history(history) is True


@pytest.mark.django_db
def test_blackening_history_does_not_repair_unknown_config_fields(
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    malformed_config = {
        "kind": segment_state.OUTSIDE_FRAME_BLACKENING_KIND,
        "legacy_unknown": "preserve-for-audit",
    }
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=malformed_config,
    )

    assert segment_state.is_outside_frame_blackening_history(history) is True
    history.refresh_from_db()
    assert history.config == malformed_config


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_reuses_active_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")

    submitted: list[Callable[[], bool]] = []

    def fake_submit(fn: Callable[[], bool]) -> types.SimpleNamespace:
        submitted.append(fn)
        return types.SimpleNamespace()

    monkeypatch.setattr(
        jobs._executor,
        "submit",
        fake_submit,
    )

    first = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)
    second = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert first.status == "queued"
    assert second.status == "already_queued"
    assert second.history_id == first.history_id
    assert len(submitted) == 1


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_returns_busy_for_other_reprocessing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")
    other_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        task_id="other-reprocessing-task",
        config={"kind": "mask_video"},
    )
    submit = Mock(side_effect=AssertionError("busy video must not dispatch"))
    monkeypatch.setattr(jobs._executor, "submit", submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.status == "busy"
    assert result.history_id == other_history.pk
    assert result.task_id == "other-reprocessing-task"
    submit.assert_not_called()


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_expires_stale_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")
    stale_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        task_id="stale-task",
        config=segment_state.blackening_history_config(only_validated=False),
    )
    VideoProcessingHistory.objects.filter(pk=stale_history.pk).update(
        created_at=timezone.now() - jobs.STALE_REBUILD_TIMEOUT - timedelta(minutes=1)
    )

    def fake_submit(_fn: Callable[[], bool]) -> types.SimpleNamespace:
        return types.SimpleNamespace()

    monkeypatch.setattr(
        jobs._executor,
        "submit",
        fake_submit,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    stale_history.refresh_from_db()
    assert stale_history.status == VideoProcessingHistory.STATUS_FAILURE
    assert result.status == "queued"
    assert result.history_id != stale_history.pk


@pytest.mark.django_db(transaction=True)
def test_dispatch_video_post_validation_rebuild_expires_stale_running_history_and_rolls_back_frames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None
    (frame_dir / "frame_0000000.jpg").write_bytes(b"partial")
    Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        is_extracted=True,
    )
    state = video.get_or_create_state()
    state.frames_initialized = True
    state.frame_count = 1
    state.frames_extracted = True
    state.save(
        update_fields=[
            "frames_initialized",
            "frame_count",
            "frames_extracted",
        ]
    )
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")
    running_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_RUNNING,
        task_id="running-blackening-task",
        config=segment_state.blackening_history_config(only_validated=False),
    )
    VideoProcessingHistory.objects.filter(pk=running_history.pk).update(
        created_at=timezone.now()
        - jobs.STALE_REBUILD_RUNNING_TIMEOUT
        - timedelta(minutes=1)
    )

    def fake_submit(_fn: Callable[[], bool]) -> types.SimpleNamespace:
        return types.SimpleNamespace()

    monkeypatch.setattr(
        jobs._executor,
        "submit",
        fake_submit,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    running_history.refresh_from_db()
    assert running_history.status == VideoProcessingHistory.STATUS_FAILURE
    assert result.status == "queued"
    assert result.history_id != running_history.pk
    assert not frame_dir.exists()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db(transaction=True)
def test_run_video_post_validation_rebuild_rolls_back_frames_when_rebuild_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None

    def fake_create_video_without_outside_frames(
        video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        assert only_validated is False
        assert outside_intervals is None
        (frame_dir / "frame_0000000.jpg").write_bytes(b"blackened")
        Frame.objects.create(
            video=video_obj,
            frame_number=0,
            relative_path="frame_0000000.jpg",
            is_extracted=True,
        )
        state = video_obj.get_or_create_state()
        state.frames_initialized = True
        state.frame_count = 1
        state.frames_extracted = True
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
            ]
        )
        return False

    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )

    assert (
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk)
        is False
    )

    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    assert not frame_dir.exists()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.frames_extracted is False
    assert not Frame.objects.filter(video=video, is_extracted=True).exists()


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_defers_when_stream_lease_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    create_video_stream_lease(video, file_type="processed", ttl_seconds=120)
    rebuild = Mock(side_effect=AssertionError("must not rebuild during stream"))
    monkeypatch.setattr(jobs, "rebuild_processed_video_without_outside_frames", rebuild)
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )

    with pytest.raises(MediaOperationDeferred):
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk)

    rebuild.assert_not_called()
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_PENDING
    assert "media operation leases are active" in history.details


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_accepts_valid_processed_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    outside_label, _ = Label.objects.get_or_create(name="outside")
    source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
    black_frame = Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path="frame_0000000.jpg",
        is_extracted=False,
    )
    ImageClassificationAnnotation.objects.create(
        frame=black_frame,
        label=outside_label,
        information_source=source,
        value=True,
    )

    def fake_create_video_without_outside_frames(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        assert only_validated is False
        assert outside_intervals == [(0, 1)]
        return True

    def fake_ensure_local_processed_video_file(
        video_obj: VideoFile,
    ) -> _ProcessedVideoContext:
        return _ensure_local_processed_context(video_obj, tmp_path)

    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    monkeypatch.setattr(
        jobs,
        "ensure_local_processed_video_file",
        fake_ensure_local_processed_video_file,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
        _valid_video_stream_info,
    )
    sampled_frame_numbers: list[int] = []

    def fake_capture_frame(_path: Path, frame_number: int):
        sampled_frame_numbers.append(frame_number)
        return __import__("numpy").zeros((4, 4, 3), dtype="uint8")

    monkeypatch.setattr(jobs, "_capture_frame", fake_capture_frame)

    def fake_censor_outside_video_frames(
        _video: VideoFile,
        *,
        only_validated: bool = False,
    ) -> bool:
        return True

    def fake_verify_outside_frames_blackened(
        _video: VideoFile,
        *,
        only_validated: bool = False,
        tolerance: int = 8,
    ) -> None:
        return None

    monkeypatch.setattr(
        jobs, "censor_outside_video_frames", fake_censor_outside_video_frames
    )
    monkeypatch.setattr(
        jobs, "_verify_outside_frames_blackened", fake_verify_outside_frames_blackened
    )

    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )

    assert (
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk) is True
    )
    assert sampled_frame_numbers == [0]
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS
    video.refresh_from_db()
    state = video.get_or_create_state()
    assert state.outside_segments_removed is True
    assert state.segment_annotations_validated is True


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_reuses_merged_intervals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    merged_intervals = [(10, 20), (40, 50)]
    merge_calls: list[tuple[int | None, bool]] = []
    create_calls: list[tuple[bool, Sequence[tuple[int, int]] | None]] = []
    verify_calls: list[tuple[bool, Sequence[tuple[int, int]] | None, int]] = []
    frame_blackening_calls: list[bool] = []

    def fake_merge(
        video_obj: VideoFile,
        *,
        only_validated: bool = False,
    ) -> list[tuple[int, int]]:
        merge_calls.append((video_obj.pk, only_validated))
        return list(merged_intervals)

    def fake_create_video_without_outside_frames(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        create_calls.append((only_validated, outside_intervals))
        return True

    def fake_verify_processed_video_contract(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
        tolerance: int = 8,
    ) -> None:
        verify_calls.append((only_validated, outside_intervals, tolerance))

    monkeypatch.setattr(jobs, "_merge_outside_frame_intervals", fake_merge)
    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    monkeypatch.setattr(
        jobs,
        "_verify_processed_video_contract",
        fake_verify_processed_video_contract,
    )

    def fake_censor_outside_video_frames(
        _video: VideoFile,
        *,
        only_validated: bool = False,
    ) -> bool:
        frame_blackening_calls.append(only_validated)
        return True

    def fake_verify_outside_frames_blackened(
        _video: VideoFile,
        *,
        only_validated: bool = False,
        tolerance: int = 8,
    ) -> None:
        return None

    monkeypatch.setattr(
        jobs,
        "censor_outside_video_frames",
        fake_censor_outside_video_frames,
    )
    monkeypatch.setattr(
        jobs,
        "_verify_outside_frames_blackened",
        fake_verify_outside_frames_blackened,
    )

    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )

    assert (
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk) is True
    )
    assert merge_calls == [(video.pk, False)]
    assert create_calls == [(False, merged_intervals)]
    assert verify_calls == [(False, merged_intervals, 8)]
    assert frame_blackening_calls == [False]


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_queues_deferred_temporal_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)

    def fake_create_video_without_outside_frames(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        assert only_validated is False
        assert outside_intervals is None
        return True

    def fake_ensure_local_processed_video_file(
        video_obj: VideoFile,
    ) -> _ProcessedVideoContext:
        return _ensure_local_processed_context(video_obj, tmp_path)

    submitted: list[Callable[[], bool]] = []

    def fake_submit(fn: Callable[[], bool]) -> types.SimpleNamespace:
        submitted.append(fn)
        return types.SimpleNamespace()

    monkeypatch.setenv("VIDEO_TEMPORAL_INFERENCE_JOB_MODE", "thread")
    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    monkeypatch.setattr(
        jobs,
        "ensure_local_processed_video_file",
        fake_ensure_local_processed_video_file,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
        _valid_video_stream_info,
    )
    monkeypatch.setattr(
        temporal_jobs._executor,
        "submit",
        fake_submit,
    )

    rebuild_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )
    deferred_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        task_id="",
        config=temporal_jobs._temporal_history_config(
            model_meta_id=123,
            replace_prediction_segments=True,
            delete_frames_after=True,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={"temporal_model": "markov"},
            raw_temporal_options={"temporal_model": "markov"},
            queue="inference",
            frame_source_mode="stream",
            deferred_reason=temporal_jobs.TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
            blocked_by_history_id=rebuild_history.pk,
        ),
    )

    assert (
        jobs._run_video_post_validation_rebuild(
            video.pk,
            history_id=rebuild_history.pk,
        )
        is True
    )

    rebuild_history.refresh_from_db()
    deferred_history.refresh_from_db()
    assert rebuild_history.status == VideoProcessingHistory.STATUS_SUCCESS
    assert deferred_history.status == VideoProcessingHistory.STATUS_PENDING
    assert deferred_history.task_id
    assert "deferred_reason" not in deferred_history.config
    assert (
        deferred_history.config["released_after_rebuild_history_id"]
        == rebuild_history.pk
    )
    assert len(submitted) == 1


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_failure_fails_deferred_temporal_inference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)

    def fake_create_video_without_outside_frames(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        assert only_validated is False
        assert outside_intervals is None
        return False

    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    rebuild_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )
    deferred_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_AI_TEMPORAL_INFERENCE,
        status=VideoProcessingHistory.STATUS_PENDING,
        task_id="",
        config=temporal_jobs._temporal_history_config(
            model_meta_id=123,
            replace_prediction_segments=True,
            delete_frames_after=True,
            ocr_frame_fraction=0.001,
            ocr_cap=10,
            temporal_options={},
            raw_temporal_options={},
            queue="inference",
            frame_source_mode="stream",
            deferred_reason=temporal_jobs.TEMPORAL_INFERENCE_DEFERRED_REASON_REPROCESSING,
            blocked_by_history_id=rebuild_history.pk,
        ),
    )

    assert (
        jobs._run_video_post_validation_rebuild(
            video.pk,
            history_id=rebuild_history.pk,
        )
        is False
    )

    deferred_history.refresh_from_db()
    assert deferred_history.status == VideoProcessingHistory.STATUS_FAILURE
    assert "required frame rebuild failed" in deferred_history.details


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_rejects_processed_output_without_video_stream(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)

    def fake_create_video_without_outside_frames(
        _video_obj: VideoFile,
        *,
        only_validated: bool = False,
        outside_intervals: Sequence[tuple[int, int]] | None = None,
    ) -> bool:
        assert only_validated is False
        assert outside_intervals is None
        return True

    def fake_ensure_local_processed_video_file(
        video_obj: VideoFile,
    ) -> _ProcessedVideoContext:
        return _ensure_local_processed_context(video_obj, tmp_path)

    monkeypatch.setattr(
        jobs,
        "rebuild_processed_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )
    monkeypatch.setattr(
        jobs,
        "ensure_local_processed_video_file",
        fake_ensure_local_processed_video_file,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.ffmpeg_wrapper.get_stream_info",
        _audio_stream_info,
    )

    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state.blackening_history_config(only_validated=False),
    )

    with pytest.raises(RuntimeError, match="no probeable video stream"):
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk)
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
    video.refresh_from_db()
    state = video.get_or_create_state()
    assert state.outside_segments_removed is False
    assert state.segment_annotations_validated is False


@pytest.mark.django_db
def test_outside_blackening_verification_uses_frame_annotation_targets(
    tmp_path: Path,
) -> None:
    video = _create_video_for_post_validation(tmp_path)
    frame_dir = video.get_frame_dir_path()
    assert frame_dir is not None

    import cv2
    import numpy as np

    black_path = frame_dir / "frame_0000000.jpg"
    white_path = frame_dir / "frame_0000001.jpg"
    cv2.imwrite(black_path.as_posix(), np.zeros((4, 4, 3), dtype=np.uint8))
    cv2.imwrite(white_path.as_posix(), np.full((4, 4, 3), 255, dtype=np.uint8))

    black_frame = Frame.objects.create(
        video=video,
        frame_number=0,
        relative_path=black_path.name,
        is_extracted=True,
    )
    white_frame = Frame.objects.create(
        video=video,
        frame_number=1,
        relative_path=white_path.name,
        is_extracted=True,
    )
    outside_label, _ = Label.objects.get_or_create(name="outside")
    source, _ = InformationSource.objects.get_or_create(name="manual_annotation")
    ImageClassificationAnnotation.objects.create(
        frame=black_frame,
        label=outside_label,
        information_source=source,
        value=True,
    )

    jobs._verify_outside_frames_blackened(video)

    ImageClassificationAnnotation.objects.create(
        frame=white_frame,
        label=outside_label,
        information_source=source,
        value=True,
    )
    with pytest.raises(RuntimeError, match="outside frames blackened"):
        jobs._verify_outside_frames_blackened(video)
