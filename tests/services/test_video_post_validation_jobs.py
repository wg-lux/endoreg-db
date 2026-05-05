from __future__ import annotations

import types
import uuid
from unittest.mock import Mock

import pytest

from endoreg_db.models import Center, Frame, VideoFile
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


def test_dispatch_video_post_validation_rebuild_inline(monkeypatch):
    runner = Mock(return_value=True)
    monkeypatch.setattr(jobs, "_run_video_post_validation_rebuild", runner)
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "inline")

    result = jobs.dispatch_video_post_validation_rebuild(video_id=123)

    assert result.mode == "inline"
    assert result.status == "completed"
    assert result.video_id == 123
    runner.assert_called_once_with(123, only_validated=False)


def test_dispatch_video_post_validation_rebuild_thread(monkeypatch):
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

    result = jobs.dispatch_video_post_validation_rebuild(video_id=456)
    assert result.mode == "thread"
    assert result.status == "queued"
    assert result.video_id == 456
    assert "fn" in submitted

    # Execute the captured callable to verify background payload works.
    submitted["fn"]()
    runner.assert_called_once_with(456, only_validated=False)


def test_dispatch_video_post_validation_rebuild_celery(monkeypatch):
    monkeypatch.setenv("VIDEO_POST_VALIDATION_JOB_MODE", "celery")

    fake_async_result = types.SimpleNamespace(id="celery-task-xyz")
    fake_task = types.SimpleNamespace(delay=Mock(return_value=fake_async_result))
    monkeypatch.setattr(
        "endoreg_db.tasks.run_video_post_validation_rebuild_task",
        fake_task,
        raising=False,
    )

    result = jobs.dispatch_video_post_validation_rebuild(video_id=789)

    assert result.mode == "celery"
    assert result.status == "queued"
    assert result.task_id == "celery-task-xyz"
    assert result.video_id == 789
    fake_task.delay.assert_called_once_with(789, only_validated=False)


def test_dispatch_video_post_validation_rebuild_celery_falls_back_to_thread(
    monkeypatch,
):
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

    result = jobs.dispatch_video_post_validation_rebuild(video_id=321)

    assert result.mode == "thread"
    assert result.status == "queued"
    assert result.video_id == 321
    assert "fn" in submitted
    submitted["fn"]()
    runner.assert_called_once_with(321, only_validated=False)


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

    assert jobs._run_video_post_validation_rebuild(video.pk) is True


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

    with pytest.raises(RuntimeError, match="non-recreatable"):
        jobs._run_video_post_validation_rebuild(video.pk)
