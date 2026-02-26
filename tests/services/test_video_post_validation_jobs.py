from __future__ import annotations

import types
from unittest.mock import Mock


from endoreg_db.services import video_post_validation_jobs as jobs


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
