from __future__ import annotations

import types
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from endoreg_db.models import Center, Frame, VideoFile, VideoProcessingHistory
from endoreg_db.services import video_post_validation_jobs as jobs


def _create_video_for_post_validation(tmp_path):
    center = Center.objects.create(
        name=f"post-validation-center-{uuid.uuid4().hex[:8]}",
        display_name="Post Validation Center",
    )
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    return VideoFile.objects.create(
        center=center,
        video_hash=f"post-validation-{uuid.uuid4().hex}",
        frame_count=2,
        frame_dir=str(frame_dir),
    )


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_inline(monkeypatch, tmp_path):
    video = _create_video_for_post_validation(tmp_path)
    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "inline")

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "inline"
    assert result.status == "completed"
    assert result.video_id == video.pk
    assert result.history_id is not None
    runner.assert_called_once_with(
        video.pk,
        only_validated=False,
        history_id=result.history_id,
    )


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_thread(monkeypatch, tmp_path):
    video = _create_video_for_post_validation(tmp_path)
    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")

    submitted = {}

    def _fake_submit(fn):
        submitted["fn"] = fn

        class _FakeFuture:
            def done(self):
                return True

        return _FakeFuture()

    monkeypatch.setattr(jobs._executor, "submit", _fake_submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)
    assert result.mode == "thread"
    assert result.status == "queued"
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
def test_dispatch_video_post_validation_rebuild_celery(monkeypatch, tmp_path):
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")

    fake_async_result = types.SimpleNamespace(id="celery-task-xyz")
    fake_task = types.SimpleNamespace(delay=Mock(return_value=fake_async_result))
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        fake_task,
        raising=False,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "celery"
    assert result.status == "queued"
    assert result.task_id == "celery-task-xyz"
    assert result.video_id == video.pk
    assert result.history_id is not None
    fake_task.delay.assert_called_once_with(
        video.pk,
        only_validated=False,
        history_id=result.history_id,
    )
    history = VideoProcessingHistory.objects.get(pk=result.history_id)
    assert history.task_id == "celery-task-xyz"


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_celery_falls_back_to_thread(
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")

    class _BrokenTask:
        def delay(self, *args, **kwargs):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        _BrokenTask(),
        raising=False,
    )

    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    submitted = {}

    def _fake_submit(fn):
        submitted["fn"] = fn
        return types.SimpleNamespace()

    monkeypatch.setattr(jobs._executor, "submit", _fake_submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert result.mode == "thread"
    assert result.status == "queued"
    assert result.video_id == video.pk
    assert result.history_id is not None
    assert "fn" in submitted
    submitted["fn"]()
    runner.assert_called_once_with(
        video.pk,
        only_validated=False,
        history_id=result.history_id,
    )


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_reuses_active_history(
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")

    submitted = []
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        lambda fn: submitted.append(fn) or types.SimpleNamespace(),
    )

    first = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)
    second = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    assert first.status == "queued"
    assert second.status == "already_queued"
    assert second.history_id == first.history_id
    assert len(submitted) == 1


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_returns_busy_for_other_reprocessing(
    monkeypatch,
    tmp_path,
):
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
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")
    stale_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        task_id="stale-task",
        config=jobs._blackening_history_config(only_validated=False),
    )
    VideoProcessingHistory.objects.filter(pk=stale_history.pk).update(
        created_at=timezone.now() - jobs.STALE_REBUILD_TIMEOUT - timedelta(minutes=1)
    )
    monkeypatch.setattr(
        jobs._executor,
        "submit",
        lambda fn: types.SimpleNamespace(),
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    stale_history.refresh_from_db()
    assert stale_history.status == VideoProcessingHistory.STATUS_FAILURE
    assert result.status == "queued"
    assert result.history_id != stale_history.pk


@pytest.mark.django_db
def test_dispatch_video_post_validation_rebuild_does_not_expire_stale_running_history(
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "thread")
    running_history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_RUNNING,
        task_id="running-blackening-task",
        config=jobs._blackening_history_config(only_validated=False),
    )
    VideoProcessingHistory.objects.filter(pk=running_history.pk).update(
        created_at=timezone.now() - jobs.STALE_REBUILD_TIMEOUT - timedelta(minutes=1)
    )
    submit = Mock(side_effect=AssertionError("running job must not dispatch"))
    monkeypatch.setattr(jobs._executor, "submit", submit)

    result = jobs.dispatch_video_post_validation_rebuild(video_id=video.pk)

    running_history.refresh_from_db()
    assert running_history.status == VideoProcessingHistory.STATUS_RUNNING
    assert result.status == "already_queued"
    assert result.history_id == running_history.pk
    submit.assert_not_called()


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_accepts_stable_extracted_frames(
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)

    def fake_create_video_without_outside_frames(video_obj, *, only_validated=False):
        frame_dir = video_obj.get_frame_dir_path()
        assert frame_dir is not None
        for frame_number in range(2):
            relative_path = f"frame_{frame_number:07d}.jpg"
            (frame_dir / relative_path).write_bytes(b"frame")
            Frame.objects.update_or_create(
                video=video_obj,
                frame_number=frame_number,
                defaults={
                    "relative_path": relative_path,
                    "is_extracted": True,
                },
            )
        state = video_obj.get_or_create_state()
        state.frames_initialized = True
        state.frame_count = 2
        state.frames_extracted = True
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
            ]
        )
        return True

    monkeypatch.setattr(
        VideoFile,
        "create_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )

    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=jobs._blackening_history_config(only_validated=False),
    )

    assert (
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk) is True
    )
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_SUCCESS


@pytest.mark.django_db
def test_run_video_post_validation_rebuild_rejects_missing_extracted_frame_file(
    monkeypatch,
    tmp_path,
):
    video = _create_video_for_post_validation(tmp_path)

    def fake_create_video_without_outside_frames(video_obj, *, only_validated=False):
        for frame_number in range(2):
            Frame.objects.update_or_create(
                video=video_obj,
                frame_number=frame_number,
                defaults={
                    "relative_path": f"frame_{frame_number:07d}.jpg",
                    "is_extracted": True,
                },
            )
        state = video_obj.get_or_create_state()
        state.frames_initialized = True
        state.frame_count = 2
        state.frames_extracted = True
        state.save(
            update_fields=[
                "frames_initialized",
                "frame_count",
                "frames_extracted",
            ]
        )
        return True

    monkeypatch.setattr(
        VideoFile,
        "create_video_without_outside_frames",
        fake_create_video_without_outside_frames,
    )

    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=jobs._blackening_history_config(only_validated=False),
    )

    with pytest.raises(RuntimeError, match="non-recreatable"):
        jobs._run_video_post_validation_rebuild(video.pk, history_id=history.pk)
    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_FAILURE
