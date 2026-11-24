"""Scope, path, lifecycle and fencing contract of frame annotation export jobs."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.video_frame_export import export_config, export_result
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.export.frames.export_frames_with_labels import (
    AnnotationExporterClient,
    ExportJobFailedError,
)
from endoreg_db.models import (
    Center,
    FrameAnnotationExportJob,
    PortalUserInfo,
    VideoFile,
    VideoState,
)
from endoreg_db.services.media import frame_annotation_export_jobs as jobs
from endoreg_db.services.media.export_annotated import ExportConflictError
from endoreg_db.utils.file_operations import advisory_file_lock

pytestmark = pytest.mark.django_db

Status = FrameAnnotationExportJob.Status


class _RecordingClient(AnnotationExporterClient):
    """Writes nothing; returns locations beneath the requested run directory."""

    def __init__(self, on_run: Callable[[export_config], None] | None = None) -> None:
        super().__init__()
        self.configs: list[export_config] = []
        self._on_run = on_run

    def run_export(self, config: export_config) -> export_result:
        self.configs.append(config)
        if self._on_run is not None:
            self._on_run(config)
        output_dir = Path(str(config.output_dir))
        return export_result(
            output_path=output_dir / Path(str(config.output_path)),
            row_count=3,
            success=True,
            exported_frame_count=3,
            frame_output_dir=output_dir / "frames",
        )


class _FailingClient(AnnotationExporterClient):
    def run_export(self, config: export_config) -> export_result:
        raise ExportJobFailedError("annotation export failed")


@pytest.fixture
def center() -> Center:
    key = f"export-job-center-{uuid4().hex[:8]}"
    return Center.objects.create(name=key, center_key=key)


@pytest.fixture
def foreign_center() -> Center:
    key = f"export-job-foreign-{uuid4().hex[:8]}"
    return Center.objects.create(name=key, center_key=key)


@pytest.fixture
def center_staff(center: Center) -> User:
    user = User.objects.create_user(username=f"center-staff-{uuid4().hex[:8]}")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    return user


@pytest.fixture
def admin() -> User:
    return User.objects.create_user(
        username=f"export-admin-{uuid4().hex[:8]}", is_staff=True
    )


@pytest.fixture
def run_name() -> str:
    return f"run_{uuid4().hex[:12]}"


def _payload(run_name: str, **overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "output_dir": run_name,
        "output_path": "annotations.csv",
        "export_profile": "pts_dataset_v1",
        "information_source_name": "manual_annotation",
    }
    payload.update(overrides)
    return payload


def _job(
    user: User, center: Center, run_name: str, status: str = Status.QUEUED.value
) -> FrameAnnotationExportJob:
    job = FrameAnnotationExportJob.objects.create(
        requested_by=user,
        center=center,
        output_dir=run_name,
        config=_payload(run_name, center_key=center.center_key),
    )
    FrameAnnotationExportJob.objects.filter(pk=job.pk).update(status=status)
    job.refresh_from_db()
    return job


def _held_run_lock(run_name: str) -> AbstractContextManager[None]:
    return advisory_file_lock(lock_path=jobs.run_lock_path(run_name), timeout_seconds=0)


@pytest.fixture
def no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[FrameAnnotationExportJob]]:
    dispatched: list[FrameAnnotationExportJob] = []
    monkeypatch.setattr(jobs, "_dispatch", dispatched.append)
    yield dispatched


@pytest.mark.parametrize("scope", ["center_staff_own_center", "admin_all_centers"])
def test_prepare_accepts_one_scope_within_user_access(
    scope: str, center: Center, center_staff: User, admin: User, run_name: str
) -> None:
    if scope == "center_staff_own_center":
        prepared = jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key), user=center_staff
        )
        assert prepared.center == center
    else:
        prepared = jobs.prepare_frame_annotation_export(
            _payload(run_name, all_centers=True), user=admin
        )
        assert prepared.center is None
    assert prepared.run_name == run_name


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"all_centers": True, "center_key": "both"},
        {"load_base_data": True},
        {"output_dir": "nested/run"},
        {"output_dir": "../escape"},
        {"output_dir": ".locks"},
        {"output_dir": None},
        {"output_path": "sub/annotations.csv"},
        {"center_key": "unknown-center"},
    ],
)
def test_prepare_rejects_invalid_scope_or_location(
    overrides: dict[str, object], center_staff: User, run_name: str
) -> None:
    with pytest.raises(ValidationError):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, **overrides), user=center_staff
        )


def test_prepare_rejects_unknown_contract_fields(
    center: Center, center_staff: User, run_name: str
) -> None:
    with pytest.raises(PydanticValidationError):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key, unexpected=True),
            user=center_staff,
        )


@pytest.mark.parametrize("scope", ["foreign_center", "all_centers"])
def test_center_staff_cannot_leave_their_centers(
    scope: str, foreign_center: Center, center_staff: User, run_name: str
) -> None:
    overrides: dict[str, object] = (
        {"center_key": foreign_center.center_key}
        if scope == "foreign_center"
        else {"all_centers": True}
    )
    with pytest.raises(PermissionDenied):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, **overrides), user=center_staff
        )


def _video(center: Center) -> VideoFile:
    return VideoFile.objects.create(
        center=center,
        state=VideoState.objects.create(),
        raw_video_hash=f"export-job-video-{uuid4().hex[:8]}",
    )


def test_prepare_accepts_video_of_the_export_center(
    center: Center, center_staff: User, run_name: str
) -> None:
    video = _video(center)

    prepared = jobs.prepare_frame_annotation_export(
        _payload(run_name, center_key=center.center_key, video_id=video.pk),
        user=center_staff,
    )

    assert prepared.payload.video_id == video.pk


def test_prepare_rejects_unknown_video(
    center: Center, center_staff: User, run_name: str
) -> None:
    with pytest.raises(ValidationError, match="Unknown video_id"):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key, video_id=999_999_999),
            user=center_staff,
        )


def test_prepare_rejects_video_outside_the_export_center(
    center: Center, foreign_center: Center, center_staff: User, run_name: str
) -> None:
    video = _video(foreign_center)

    with pytest.raises(PermissionDenied, match="video_id"):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key, video_id=video.pk),
            user=center_staff,
        )


def test_prepare_rejects_video_with_unfinalized_annotations(
    center: Center,
    center_staff: User,
    run_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video(center)

    def cleanup_running(_video: VideoFile) -> str:
        return "cleanup_running"

    monkeypatch.setattr(jobs, "resolve_segment_annotation_status", cleanup_running)

    with pytest.raises(ValidationError, match="cleanup_required"):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key, video_id=video.pk),
            user=center_staff,
        )


def test_prepare_rejects_table_symlink_escaping_export_root(
    center: Center, center_staff: User, run_name: str, tmp_path: Path
) -> None:
    run_dir = jobs.frame_annotation_export_root() / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "annotations.csv").symlink_to(tmp_path / "outside.csv")

    with pytest.raises(ValidationError):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key), user=center_staff
        )


def test_create_dispatches_after_commit_and_rejects_active_duplicate(
    center: Center,
    center_staff: User,
    run_name: str,
    no_dispatch: list[FrameAnnotationExportJob],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    payload = _payload(run_name, center_key=center.center_key)
    with django_capture_on_commit_callbacks(execute=True):
        job = jobs.create_frame_annotation_export_job(payload, user=center_staff)

    assert job.status == Status.QUEUED.value
    assert job.output_dir == run_name
    assert [dispatched.pk for dispatched in no_dispatch] == [job.pk]
    with pytest.raises(ExportConflictError):
        jobs.create_frame_annotation_export_job(payload, user=center_staff)


def test_rolled_back_creation_never_dispatches(
    center: Center,
    center_staff: User,
    run_name: str,
    no_dispatch: list[FrameAnnotationExportJob],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError, match="abort"), transaction.atomic():
            jobs.create_frame_annotation_export_job(
                _payload(run_name, center_key=center.center_key), user=center_staff
            )
            raise RuntimeError("abort")
    assert no_dispatch == []
    assert not FrameAnnotationExportJob.objects.filter(output_dir=run_name).exists()


@pytest.mark.parametrize(
    ("client", "status", "error"),
    [
        (_RecordingClient(), Status.COMPLETED.value, ""),
        (_FailingClient(), Status.FAILED.value, "annotation export failed"),
    ],
)
def test_run_records_terminal_state(
    client: AnnotationExporterClient,
    status: str,
    error: str,
    center: Center,
    center_staff: User,
    run_name: str,
) -> None:
    job = _job(center_staff, center, run_name)

    finished = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert finished.status == status
    assert finished.error == error
    assert finished.claim_token is not None
    if status == Status.COMPLETED.value:
        assert finished.result == {
            "output_path": f"{run_name}/annotations.csv",
            "row_count": 3,
            "exported_video_count": 0,
            "exported_frame_count": 3,
            "video_output_dir": None,
            "frame_output_dir": f"{run_name}/frames",
        }


def test_run_passes_confined_config_to_exporter(
    center: Center, center_staff: User, run_name: str
) -> None:
    client = _RecordingClient()
    jobs.run_frame_annotation_export_job(
        _job(center_staff, center, run_name).pk, client=client
    )

    (config,) = client.configs
    assert config.output_dir == jobs.frame_annotation_export_root() / run_name
    assert config.output_path == Path("annotations.csv")
    assert config.center_key == center.center_key
    assert config.load_base_data is False


def test_duplicate_delivery_while_worker_holds_lock_changes_nothing(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name, status=Status.RUNNING.value)
    client = _RecordingClient()

    with _held_run_lock(run_name):
        result = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert result.status == Status.RUNNING.value
    assert client.configs == []


def test_redelivery_after_worker_death_marks_job_lost(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name, status=Status.RUNNING.value)
    client = _RecordingClient()

    result = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert result.status == Status.LOST.value
    assert client.configs == []


def test_new_job_fails_while_lost_writer_still_holds_run(
    center: Center, center_staff: User, run_name: str
) -> None:
    _job(center_staff, center, run_name, status=Status.LOST.value)
    job = _job(center_staff, center, run_name)
    client = _RecordingClient()

    with _held_run_lock(run_name):
        result = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert result.status == Status.FAILED.value
    assert "still writes this run directory" in result.error
    assert client.configs == []


def test_stale_worker_cannot_overwrite_lost_job(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name)

    def mark_lost(_config: export_config) -> None:
        FrameAnnotationExportJob.objects.filter(pk=job.pk).update(
            status=Status.LOST.value
        )

    result = jobs.run_frame_annotation_export_job(
        job.pk, client=_RecordingClient(on_run=mark_lost)
    )

    assert result.status == Status.LOST.value
    assert result.result is None


def test_stale_recovery_marks_only_overdue_jobs_lost(
    center: Center, center_staff: User
) -> None:
    now = timezone.now()
    silent = _job(center_staff, center, f"run_{uuid4().hex[:8]}", Status.RUNNING.value)
    alive = _job(center_staff, center, f"run_{uuid4().hex[:8]}", Status.RUNNING.value)
    abandoned = _job(center_staff, center, f"run_{uuid4().hex[:8]}")
    waiting = _job(center_staff, center, f"run_{uuid4().hex[:8]}")
    FrameAnnotationExportJob.objects.filter(pk=silent.pk).update(
        heartbeat_at=now - jobs.FRAME_ANNOTATION_EXPORT_RUNNING_STALE_AFTER * 2
    )
    FrameAnnotationExportJob.objects.filter(pk=alive.pk).update(heartbeat_at=now)
    FrameAnnotationExportJob.objects.filter(pk=abandoned.pk).update(
        created_at=now
        - jobs.FRAME_ANNOTATION_EXPORT_QUEUED_STALE_AFTER
        - timedelta(minutes=1)
    )

    assert jobs.recover_stale_frame_annotation_export_jobs() == 2

    statuses = {
        job.pk: job.status
        for job in FrameAnnotationExportJob.objects.filter(
            pk__in=[silent.pk, alive.pk, abandoned.pk, waiting.pk]
        )
    }
    assert statuses == {
        silent.pk: Status.LOST.value,
        alive.pk: Status.RUNNING.value,
        abandoned.pk: Status.LOST.value,
        waiting.pk: Status.QUEUED.value,
    }


def test_dispatch_failure_fails_job_immediately(
    center: Center,
    center_staff: User,
    run_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db import tasks

    def refuse(*args: object, **kwargs: object) -> None:
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(tasks.run_frame_annotation_export_task, "apply_async", refuse)
    job = _job(center_staff, center, run_name)

    jobs._dispatch(job)  # pyright: ignore[reportPrivateUsage]

    job.refresh_from_db()
    assert job.status == Status.FAILED.value
    assert "broker unavailable" in job.error


def test_visible_jobs_follow_center_scope(
    center: Center,
    foreign_center: Center,
    center_staff: User,
    admin: User,
) -> None:
    own = _job(center_staff, center, f"run_{uuid4().hex[:8]}")
    foreign = _job(admin, foreign_center, f"run_{uuid4().hex[:8]}")
    every = FrameAnnotationExportJob.objects.create(
        requested_by=admin,
        all_centers=True,
        output_dir=f"run_{uuid4().hex[:8]}",
        config=_payload("every", all_centers=True),
    )

    staff_view = set(
        jobs.visible_frame_annotation_export_jobs(center_staff).values_list(
            "pk", flat=True
        )
    )
    admin_view = set(
        jobs.visible_frame_annotation_export_jobs(admin).values_list("pk", flat=True)
    )

    assert own.pk in staff_view
    assert foreign.pk not in staff_view and every.pk not in staff_view
    assert {own.pk, foreign.pk, every.pk} <= admin_view


_LOCK_HOLDER = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
sys.stdin.readline()
"""


@contextmanager
def _lock_held_by_other_process(run_name: str) -> Generator[subprocess.Popen[str]]:
    lock_path = jobs.run_lock_path(run_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", _LOCK_HOLDER, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        yield holder
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_run_lock_excludes_writer_in_separate_process_until_it_exits(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name, status=Status.RUNNING.value)
    client = _RecordingClient()

    with _lock_held_by_other_process(run_name):
        during = jobs.run_frame_annotation_export_job(job.pk, client=client)
        assert during.status == Status.RUNNING.value

    # The kernel released the lock with the process: the old worker is gone.
    after = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert after.status == Status.LOST.value
    assert client.configs == []


def test_queued_job_fails_while_separate_process_holds_run(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name)

    with _lock_held_by_other_process(run_name):
        result = jobs.run_frame_annotation_export_job(job.pk, client=_RecordingClient())

    assert result.status == Status.FAILED.value


def test_run_name_aliasing_another_directory_is_rejected(
    center: Center, center_staff: User, run_name: str
) -> None:
    root = jobs.frame_annotation_export_root()
    (root / run_name).mkdir(parents=True)
    alias = f"{run_name}_alias"
    (root / alias).symlink_to(root / run_name, target_is_directory=True)

    with pytest.raises(ValidationError, match="resolves to another directory"):
        jobs.prepare_frame_annotation_export(
            _payload(alias, center_key=center.center_key), user=center_staff
        )

    queued = _job(center_staff, center, alias)
    client = _RecordingClient()
    result = jobs.run_frame_annotation_export_job(queued.pk, client=client)
    assert result.status == Status.FAILED.value
    assert client.configs == []


def test_worker_revalidates_center_access_before_export(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name)
    PortalUserInfo.objects.get(user=center_staff).centers.clear()
    client = _RecordingClient()

    result = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert result.status == Status.FAILED.value
    assert "center scope" in result.error
    assert client.configs == []


@pytest.mark.parametrize("scope", ["center", "all_centers"])
def test_deactivated_requester_cannot_run_or_create_exports(
    scope: str,
    center: Center,
    center_staff: User,
    admin: User,
    run_name: str,
    no_dispatch: list[FrameAnnotationExportJob],
) -> None:
    requester = center_staff if scope == "center" else admin
    payload = (
        _payload(run_name, center_key=center.center_key)
        if scope == "center"
        else _payload(run_name, all_centers=True)
    )
    job = jobs.create_frame_annotation_export_job(payload, user=requester)
    requester.is_active = False
    requester.save(update_fields=["is_active"])
    client = _RecordingClient()

    result = jobs.run_frame_annotation_export_job(job.pk, client=client)

    assert result.status == Status.FAILED.value
    assert "active authenticated user" in result.error
    assert client.configs == []
    before = FrameAnnotationExportJob.objects.count()
    dispatched = len(no_dispatch)
    with pytest.raises(PermissionDenied, match="active authenticated user"):
        jobs.create_frame_annotation_export_job(payload, user=requester)
    assert FrameAnnotationExportJob.objects.count() == before
    assert len(no_dispatch) == dispatched


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected", None),
        ("only_validated", None),
        ("load_base_data", None),
        ("limit", {}),
        ("limit", []),
    ],
)
def test_model_rejects_invalid_config_values(
    field: str, value: object, center: Center, center_staff: User, run_name: str
) -> None:
    with pytest.raises(ValidationError):
        FrameAnnotationExportJob.objects.create(
            requested_by=center_staff,
            center=center,
            output_dir=run_name,
            config=_payload(run_name, center_key=center.center_key, **{field: value}),
        )


@pytest.mark.parametrize("run_name", ["../outside", "nested/run", ".locks"])
def test_lock_path_rejects_invalid_run_names(run_name: str) -> None:
    with pytest.raises(ValidationError):
        jobs.run_lock_path(run_name)


@pytest.mark.parametrize("alias", ["directory", "file"])
def test_lock_path_rejects_symlinks(
    alias: str, run_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "exports"
    monkeypatch.setattr(jobs, "frame_annotation_export_root", lambda: root)
    root.mkdir(parents=True, exist_ok=True)
    locks = root / jobs.FRAME_ANNOTATION_EXPORT_LOCK_DIR_NAME
    if alias == "directory":
        locks.symlink_to(tmp_path, target_is_directory=True)
    else:
        locks.mkdir()
        (locks / f"{run_name}.lock").symlink_to(tmp_path / "outside.lock")
    with pytest.raises(ValidationError, match="symbolic links"):
        jobs.run_lock_path(run_name)
    assert not (tmp_path / "outside.lock").exists()


def test_worker_revalidates_browser_only_config_limits(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name)
    FrameAnnotationExportJob.objects.filter(pk=job.pk).update(
        config=_payload(run_name, center_key=center.center_key, load_base_data=True)
    )
    client = _RecordingClient()
    result = jobs.run_frame_annotation_export_job(job.pk, client=client)
    assert result.status == Status.FAILED.value
    assert "load_base_data" in result.error
    assert client.configs == []


def test_table_cannot_alias_another_run(
    center: Center, center_staff: User, run_name: str
) -> None:
    root = jobs.frame_annotation_export_root()
    run_dir = root / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "annotations.csv").symlink_to(root / "another_run" / "annotations.csv")
    with pytest.raises(ValidationError, match="own run directory"):
        jobs.prepare_frame_annotation_export(
            _payload(run_name, center_key=center.center_key), user=center_staff
        )
