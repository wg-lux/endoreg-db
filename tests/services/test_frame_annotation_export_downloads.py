"""Read access contract for completed frame annotation export runs."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from endoreg_db.models import Center, FrameAnnotationExportJob, PortalUserInfo
from endoreg_db.services.media import frame_annotation_export_downloads as downloads
from endoreg_db.services.media import frame_annotation_export_jobs as jobs

pytestmark = pytest.mark.django_db

Status = FrameAnnotationExportJob.Status


@pytest.fixture
def center() -> Center:
    key = f"export-download-center-{uuid4().hex[:8]}"
    return Center.objects.create(name=key, center_key=key)


@pytest.fixture
def foreign_center() -> Center:
    key = f"export-download-foreign-{uuid4().hex[:8]}"
    return Center.objects.create(name=key, center_key=key)


@pytest.fixture
def center_staff(center: Center) -> User:
    user = User.objects.create_user(username=f"download-staff-{uuid4().hex[:8]}")
    PortalUserInfo.objects.create(user=user).centers.add(center)
    return user


@pytest.fixture
def run_name() -> str:
    return f"run_{uuid4().hex[:12]}"


def _job(
    user: User, center: Center, run_name: str, status: str = Status.COMPLETED.value
) -> FrameAnnotationExportJob:
    job = FrameAnnotationExportJob.objects.create(
        requested_by=user,
        center=center,
        output_dir=run_name,
        config={
            "output_dir": run_name,
            "output_path": "annotations.csv",
            "center_key": center.center_key,
        },
    )
    FrameAnnotationExportJob.objects.filter(pk=job.pk).update(status=status)
    job.refresh_from_db()
    return job


def _write_run(run_name: str) -> Path:
    run_dir = jobs.frame_annotation_export_root() / run_name
    (run_dir / "video_7" / "frames").mkdir(parents=True)
    (run_dir / "annotations.csv").write_text("frame,label\n1,polyp\n")
    (run_dir / "video_7" / "frames" / "frame_1.jpg").write_bytes(b"\xff\xd8jpeg")
    return run_dir


def test_completed_run_lists_regular_files_in_stable_order(
    center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    job = _job(center_staff, center, run_name)

    run = downloads.completed_export_run(job.pk, user=center_staff)

    assert downloads.list_export_run_files(run) == (
        downloads.ExportRunFile("annotations.csv", 20),
        downloads.ExportRunFile("video_7/frames/frame_1.jpg", 6),
    )


def test_foreign_center_job_is_not_found(
    foreign_center: Center, center_staff: User, run_name: str
) -> None:
    owner = User.objects.create_user(username=f"foreign-owner-{uuid4().hex[:8]}")
    _write_run(run_name)
    job = _job(owner, foreign_center, run_name)

    with pytest.raises(downloads.ExportRunNotFound):
        downloads.completed_export_run(job.pk, user=center_staff)


@pytest.mark.parametrize(
    "status", [Status.QUEUED, Status.RUNNING, Status.FAILED, Status.LOST]
)
def test_unfinished_job_is_unavailable(
    status: str, center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    job = _job(center_staff, center, run_name, status=status)

    with pytest.raises(downloads.ExportRunUnavailable):
        downloads.completed_export_run(job.pk, user=center_staff)


def test_superseded_job_is_unavailable(
    center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    older = _job(center_staff, center, run_name)
    FrameAnnotationExportJob.objects.filter(pk=older.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    _job(center_staff, center, run_name)

    with pytest.raises(downloads.ExportRunUnavailable, match="newer export job"):
        downloads.completed_export_run(older.pk, user=center_staff)


def test_missing_run_directory_is_unavailable(
    center: Center, center_staff: User, run_name: str
) -> None:
    job = _job(center_staff, center, run_name)

    with pytest.raises(downloads.ExportRunUnavailable):
        downloads.completed_export_run(job.pk, user=center_staff)


def test_symbolic_link_inside_run_fails_listing(
    center: Center, center_staff: User, run_name: str, tmp_path: Path
) -> None:
    run_dir = _write_run(run_name)
    (tmp_path / "outside.txt").write_text("outside")
    (run_dir / "video_7" / "leak.txt").symlink_to(tmp_path / "outside.txt")
    run = downloads.completed_export_run(
        _job(center_staff, center, run_name).pk, user=center_staff
    )

    with pytest.raises(downloads.ExportRunUnavailable, match="symbolic link"):
        downloads.list_export_run_files(run)
    with pytest.raises(downloads.ExportRunNotFound):
        downloads.resolve_export_run_file(run, "video_7/leak.txt")


@pytest.mark.parametrize("relative_path", ["", "/etc/passwd", "../x", "a/../b"])
def test_unsafe_file_paths_are_rejected(
    relative_path: str, center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    run = downloads.completed_export_run(
        _job(center_staff, center, run_name).pk, user=center_staff
    )

    with pytest.raises(ValidationError):
        downloads.resolve_export_run_file(run, relative_path)


@pytest.mark.parametrize("relative_path", ["missing.csv", "video_7"])
def test_non_file_paths_are_not_found(
    relative_path: str, center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    run = downloads.completed_export_run(
        _job(center_staff, center, run_name).pk, user=center_staff
    )

    with pytest.raises(downloads.ExportRunNotFound):
        downloads.resolve_export_run_file(run, relative_path)


def test_archive_contains_every_file_below_the_run_folder(
    center: Center, center_staff: User, run_name: str
) -> None:
    _write_run(run_name)
    run = downloads.completed_export_run(
        _job(center_staff, center, run_name).pk, user=center_staff
    )

    archive = b"".join(
        downloads.iter_export_run_archive(run, downloads.list_export_run_files(run))
    )

    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        assert opened.testzip() is None
        assert opened.namelist() == [
            f"{run_name}/annotations.csv",
            f"{run_name}/video_7/frames/frame_1.jpg",
        ]
        assert opened.read(f"{run_name}/video_7/frames/frame_1.jpg") == b"\xff\xd8jpeg"
