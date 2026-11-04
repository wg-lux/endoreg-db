from __future__ import annotations

import os
import time
from collections.abc import Callable, Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.core.files.base import ContentFile

from endoreg_db.config import env
from endoreg_db.models import Center, VideoFile
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.services.hub.upload_staging_orphans import (
    UploadStagingOrphanDecision,
    classify_upload_staging_unit,
    reap_upload_staging_orphan,
    run_upload_staging_orphan_cleanup,
)
from endoreg_db.utils.file_operations import atomic_write_file, safe_rmtree
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db

GRACE_SECONDS = 6 * 60 * 60
STALE_AGE_SECONDS = 2 * 24 * 60 * 60


def _backdate(path: Path, age_seconds: float = STALE_AGE_SECONDS) -> None:
    stamp = time.time() - age_seconds
    for root, directory_names, file_names in os.walk(path):
        for name in (*directory_names, *file_names):
            os.utime(Path(root) / name, (stamp, stamp), follow_symlinks=False)
    os.utime(path, (stamp, stamp))


@pytest.fixture
def orphan_dir() -> Generator[Path]:
    """A job directory left behind after its UploadJob row disappeared."""
    job = UploadJob.objects.create(
        file=ContentFile(b"uploaded-source", name="source.mp4"),
        content_type="video/mp4",
    )
    directory = Path(job.file.path).parent
    UploadJob.objects.filter(pk=job.pk).delete()
    assert directory.is_dir()
    _backdate(directory)
    yield directory
    safe_rmtree(directory, missing_ok=True)


def _classify(directory: Path):
    return classify_upload_staging_unit(
        directory,
        UUID(directory.name),
        grace_seconds=GRACE_SECONDS,
        now=time.time(),
    )


def test_unreferenced_old_job_directory_is_an_orphan(orphan_dir: Path) -> None:
    assert _classify(orphan_dir).decision is UploadStagingOrphanDecision.DELETE


def test_existing_job_directory_belongs_to_the_ledger_reaper() -> None:
    job = UploadJob.objects.create(
        file=ContentFile(b"uploaded-source", name="source.mp4"),
        content_type="video/mp4",
    )
    directory = Path(job.file.path).parent
    _backdate(directory)
    try:
        assert _classify(directory).decision is UploadStagingOrphanDecision.JOB_EXISTS
    finally:
        safe_rmtree(directory, missing_ok=True)


def _reuse_reference(directory: Path) -> None:
    UploadJob.objects.create(
        file=ContentFile(b"other", name="other.mp4"),
        content_type="video/mp4",
        processing_provenance={"stored_upload_path": str(directory / "source.mp4")},
    )


def _inventory_reference(directory: Path) -> None:
    owner = UploadJob.objects.create(
        file=ContentFile(b"other", name="other.mp4"), content_type="video/mp4"
    )
    UploadJobFile.objects.create(
        upload_job=owner,
        path=str(directory / "source.mp4"),
        role=UploadJobFile.Role.SOURCE,
    )


def _media_reference(directory: Path) -> None:
    relative = (directory / "source.mp4").relative_to(get_runtime_paths().storage)
    video = VideoFile.objects.create(
        center=Center.objects.create(name=f"orphan-{uuid4().hex}"),
        raw_video_hash=uuid4().hex,
    )
    VideoFile.objects.filter(pk=video.pk).update(raw_file=relative.as_posix())


@pytest.mark.parametrize(
    "add_reference",
    [_reuse_reference, _inventory_reference, _media_reference],
    ids=["provenance", "inventory", "media_field"],
)
def test_any_remaining_reference_blocks_deletion(
    orphan_dir: Path, add_reference: Callable[[Path], None]
) -> None:
    add_reference(orphan_dir)

    assert _classify(orphan_dir).decision is UploadStagingOrphanDecision.REFERENCED


def test_recent_orphan_is_kept(orphan_dir: Path) -> None:
    atomic_write_file(destination=orphan_dir / "late.json", content=[b"{}"])

    assert _classify(orphan_dir).decision is UploadStagingOrphanDecision.TOO_RECENT


def test_in_progress_handoff_file_blocks_deletion(orphan_dir: Path) -> None:
    atomic_write_file(destination=orphan_dir / "source.mp4.part", content=[b"x"])
    _backdate(orphan_dir)

    assert (
        _classify(orphan_dir).decision
        is UploadStagingOrphanDecision.IN_PROGRESS_HANDOFF
    )


def test_symbolic_link_blocks_deletion(orphan_dir: Path) -> None:
    (orphan_dir / "link.mp4").symlink_to(orphan_dir / "source.mp4")

    assert _classify(orphan_dir).decision is UploadStagingOrphanDecision.UNSAFE_LINK


def test_entries_outside_the_job_layout_are_only_reported() -> None:
    stray = get_runtime_paths().upload_api / f"stray-{uuid4().hex}.mp4"
    atomic_write_file(destination=stray, content=[b"stray"])
    _backdate(stray)
    try:
        result = run_upload_staging_orphan_cleanup(
            apply=True, grace_seconds=GRACE_SECONDS
        )
        item = next(item for item in result.items if item.path == stray)
        assert item.decision is UploadStagingOrphanDecision.UNRECOGNIZED_LAYOUT
        assert stray.exists()
    finally:
        stray.unlink(missing_ok=True)


@pytest.mark.parametrize("apply", [False, True])
def test_cleanup_run_deletes_orphans_only_when_applied(
    orphan_dir: Path, apply: bool
) -> None:
    result = run_upload_staging_orphan_cleanup(apply=apply, grace_seconds=GRACE_SECONDS)

    item = next(item for item in result.items if item.path == orphan_dir)
    assert item.decision is UploadStagingOrphanDecision.DELETE
    assert item.deleted is apply
    assert orphan_dir.exists() is not apply


def test_grace_period_has_a_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENDOREG_STAGING_CLEANUP_GRACE_SECONDS", "60")

    with pytest.raises(env.EnvironmentValueError):
        env.staging_cleanup_grace_seconds()


@pytest.mark.parametrize(
    ("configured", "expected"), [(None, True), ("true", True), ("false", False)]
)
def test_periodic_cleanup_gate_is_enabled_unless_disabled(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: bool
) -> None:
    if configured is None:
        monkeypatch.delenv("UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED", raising=False)
    else:
        monkeypatch.setenv("UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED", configured)

    assert env.upload_job_source_reaper_apply_enabled() is expected


def test_single_orphan_reap_revalidates_and_deletes(orphan_dir: Path) -> None:
    item = reap_upload_staging_orphan(orphan_dir, grace_seconds=GRACE_SECONDS)

    assert item.deleted
    assert not orphan_dir.exists()


def test_single_orphan_reap_keeps_referenced_directory(orphan_dir: Path) -> None:
    _inventory_reference(orphan_dir)

    item = reap_upload_staging_orphan(orphan_dir, grace_seconds=GRACE_SECONDS)

    assert item.decision is UploadStagingOrphanDecision.REFERENCED
    assert not item.deleted
    assert orphan_dir.exists()


def test_single_orphan_reap_rejects_paths_outside_the_layout() -> None:
    with pytest.raises(ValueError, match="not an upload staging unit"):
        reap_upload_staging_orphan(
            get_runtime_paths().storage / "processed_videos_final",
            grace_seconds=GRACE_SECONDS,
        )
