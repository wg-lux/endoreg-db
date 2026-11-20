from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext

from endoreg_db.import_files.file_storage.cleanup import cleanup_staging_files
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.services.media.source_deletion import (
    check_original,
    recorded_upload_sources,
)
from endoreg_db.services.hub.upload_job_files import (
    record_working_file,
    record_media_files,
    register_upload_job_file,
    register_upload_job_sources,
    registered_staging_paths,
    track_upload_job_files,
    track_working_directory,
    upload_job_file_inventory,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
)
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.hashs import get_file_hash

pytestmark = pytest.mark.django_db


@pytest.fixture
def job() -> UploadJob:
    return UploadJob.objects.create(
        file=ContentFile(b"source", name="source.pdf"), content_type="application/pdf"
    )


@pytest.fixture
def workspace() -> Generator[Path]:
    path = ensure_directory(get_runtime_paths().transcoding / uuid4().hex)
    try:
        yield path
    finally:
        safe_rmtree(path)


@pytest.mark.parametrize("fail", [False, True])
def test_workflow_records_sources_working_files_and_removal(
    job: UploadJob, workspace: Path, fail: bool
) -> None:
    sidecar = workspace / "source.json"
    sidecar.write_bytes(b"sidecar")
    job.processing_provenance = {"sidecar_path": str(sidecar)}
    job.save()
    destination = workspace / "copy.pdf"
    try:
        with track_upload_job_files(job):
            atomic_write_file(destination=destination, content=[b"working"])
            if fail:
                raise RuntimeError("consumer failed")
            cleanup_staging_files((destination,), label="test staging")
    except RuntimeError as exc:
        assert fail and str(exc) == "consumer failed"
    inventory = {entry.path: entry for entry in upload_job_file_inventory(job)}
    assert inventory[Path(job.file.path)].registered
    assert inventory[sidecar].role == UploadJobFile.Role.SIDECAR
    assert inventory[destination].role == UploadJobFile.Role.WORKING
    row = UploadJobFile.objects.get(upload_job=job, path=str(destination))
    assert (row.removed_at is None) is fail
    outside_scope = workspace / "unrelated.pdf"
    atomic_write_file(destination=outside_scope, content=[b"not owned"])
    assert not UploadJobFile.objects.filter(path=str(outside_scope)).exists()


def test_explicit_staging_in_raw_storage_is_disposable_but_canonical_media_is_retained(
    job: UploadJob,
) -> None:
    root = ensure_directory(get_runtime_paths().sensitive_report / uuid4().hex)
    working, canonical = root / "snapshot.pdf", root / "canonical.pdf"
    try:
        with track_upload_job_files(job):
            record_working_file(working)
            atomic_write_file(destination=working, content=[b"snapshot"])
            atomic_write_file(destination=canonical, content=[b"canonical"])
            cleanup_staging_files((working,), label="owned snapshot")
            with pytest.raises(ValueError, match="role"):
                cleanup_staging_files((canonical,), label="canonical")
        assert not working.exists()
        assert canonical.read_bytes() == b"canonical"
    finally:
        safe_rmtree(root)


def test_external_tool_workspace_registers_side_outputs_and_cleans_them(
    job: UploadJob, workspace: Path
) -> None:
    output, side_output = workspace / "result.pdf", workspace / "ocr.txt"
    with track_upload_job_files(job):
        with track_working_directory(workspace):
            output.write_bytes(b"result")
            side_output.write_bytes(b"plaintext OCR")
        assert UploadJobFile.objects.filter(
            upload_job=job, path=str(side_output)
        ).exists()
        cleanup_staging_files((output,), label="published workspace")
    assert not output.exists() and not side_output.exists()


def test_inventory_survives_provenance_replacement_and_retry(
    job: UploadJob, workspace: Path
) -> None:
    with track_upload_job_files(job):
        atomic_write_file(destination=workspace / "attempt-one.pdf", content=[b"one"])
    job.processing_provenance = {"custom_marker": "later update"}
    job.save()
    with track_upload_job_files(job):
        atomic_write_file(destination=workspace / "attempt-two.pdf", content=[b"two"])
    paths = {entry.path for entry in upload_job_file_inventory(job)}
    assert {workspace / "attempt-one.pdf", workspace / "attempt-two.pdf"} <= paths


def test_shared_registered_file_is_not_deleted(job: UploadJob, workspace: Path) -> None:
    other = UploadJob.objects.create(content_type="application/pdf")
    path = workspace / "shared.pdf"
    path.write_bytes(b"shared")
    for owner in (job, other):
        register_upload_job_file(owner.pk, path, role=UploadJobFile.Role.WORKING)
    with pytest.raises(ValueError, match="shared"):
        cleanup_staging_files((path,), label="shared")
    assert path.exists()


def test_registration_rejects_symbolic_aliases(job: UploadJob, workspace: Path) -> None:
    target = workspace / "target.pdf"
    target.write_bytes(b"retained")
    alias = workspace / "alias.pdf"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        register_upload_job_file(job.pk, alias)
    assert not UploadJobFile.objects.filter(upload_job=job).exists()


def test_documented_staging_does_not_require_content_hash_evidence(
    job: UploadJob, workspace: Path
) -> None:
    path = workspace / "working.pdf"
    path.write_bytes(b"initial")
    register_upload_job_file(job.pk, path, role=UploadJobFile.Role.WORKING)
    path.write_bytes(b"later bytes from the same workflow")
    cleanup_staging_files((path,), label="documented staging")
    assert not path.exists()


def test_sources_registration_is_idempotent(job: UploadJob) -> None:
    register_upload_job_sources(job)
    register_upload_job_sources(job)
    assert UploadJobFile.objects.filter(upload_job=job).count() == 1


def test_cleanup_preserves_another_active_attempt(
    job: UploadJob, workspace: Path
) -> None:
    path = workspace / "running.pdf"
    path.write_bytes(b"in progress")
    register_upload_job_file(job.pk, path, role=UploadJobFile.Role.WORKING)
    job.status = UploadJob.Status.PROCESSING
    job.save()
    with pytest.raises(ValueError, match="active upload job"):
        cleanup_staging_files((path,), label="outside attempt")
    assert path.exists()


def test_cleanup_rejects_overlapping_registered_workspaces(
    job: UploadJob, workspace: Path
) -> None:
    other = UploadJob.objects.create(content_type="application/pdf")
    for owner in (job, other):
        register_upload_job_file(
            owner.pk, workspace, role=UploadJobFile.Role.WORKING, is_directory=True
        )
    path = workspace / "shared.pdf"
    path.write_bytes(b"shared workspace")
    with pytest.raises(ValueError, match="shared"):
        cleanup_staging_files((path,), label="overlap")
    assert path.exists()


def test_explicit_removal_accepts_documented_sidecar_with_different_content(
    job: UploadJob, workspace: Path
) -> None:
    job.content_hash = get_file_hash(job.file)
    job.status = UploadJob.Status.ANONYMIZED
    job.save()
    register_upload_job_sources(job)
    sidecar = workspace / "source.json"
    sidecar.write_bytes(b"different sidecar contents")
    register_upload_job_file(job.pk, sidecar, role=UploadJobFile.Role.SIDECAR)
    with CaptureQueriesContext(connection) as queries:
        sources = recorded_upload_sources([job], digest=job.content_hash)
    assert not any(
        '"endoreg_db_videofile"' in query["sql"]
        or '"endoreg_db_rawpdffile"' in query["sql"]
        for query in queries.captured_queries
    )
    assert sources == {sidecar, Path(job.file.path)}
    for source in sources:
        check_original(source, job.content_hash)


def test_media_inventory_includes_retained_frames_and_previous_generations(
    job: UploadJob,
) -> None:
    center = Center.objects.create(name=f"inventory-{job.pk}", display_name="Inventory")
    video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
    frame = Frame.objects.create(
        video=video, frame_number=0, relative_path="frame.jpg", is_extracted=True
    )
    video.meta = {
        "processed_generation_cleanup": [
            {
                "source_name": "processed_videos_final/old.mp4",
                "source_sha256": "a" * 64,
                "replacement_name": "processed_videos_final/new.mp4",
                "replacement_sha256": "b" * 64,
            }
        ]
    }
    record_media_files(job, video)
    entries = {entry.path: entry.role for entry in upload_job_file_inventory(job)}
    assert entries[frame.file_path] == UploadJobFile.Role.RETAINED
    assert (
        entries[get_runtime_paths().storage / "processed_videos_final/old.mp4"]
        == UploadJobFile.Role.RETAINED
    )


@pytest.mark.django_db(transaction=True)
def test_parallel_registration_keeps_both_files(
    job: UploadJob, workspace: Path
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL")
    barrier = Barrier(2)

    def register(name: str) -> None:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            register_upload_job_file(
                job.pk, workspace / name, role=UploadJobFile.Role.WORKING
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(register, ["one.pdf", "two.pdf"]))
    assert UploadJobFile.objects.filter(upload_job=job).count() == 2


def test_registered_sources_need_only_one_inventory_query(
    job: UploadJob, workspace: Path
) -> None:
    register_upload_job_sources(job)
    for name in ("one.pdf", "two.pdf", "three.pdf"):
        register_upload_job_file(
            job.pk, workspace / name, role=UploadJobFile.Role.WORKING
        )
    with CaptureQueriesContext(connection) as queries:
        register_upload_job_sources(job)
    assert len(queries) == 1


def test_staging_cleanup_ignores_unrelated_workspace(
    job: UploadJob, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unrelated = workspace / "unrelated"
    register_upload_job_file(
        job.pk, unrelated, role=UploadJobFile.Role.WORKING, is_directory=True
    )

    def unexpected_inventory_read(_job: UploadJob) -> tuple[()]:
        raise AssertionError("Unrelated workspace must not be inspected")

    monkeypatch.setattr(
        "endoreg_db.services.hub.upload_job_files.upload_job_file_inventory",
        unexpected_inventory_read,
    )
    candidate = workspace / "candidate.pdf"
    assert registered_staging_paths((candidate,)) == (candidate,)
    with CaptureQueriesContext(connection) as queries:
        assert registered_staging_paths(()) == ()
    assert len(queries) == 0


@pytest.mark.parametrize(
    "role", [UploadJobFile.Role.RETAINED, UploadJobFile.Role.QUARANTINE]
)
def test_explicit_removal_preserves_registered_protected_source(
    job: UploadJob, role: UploadJobFile.Role
) -> None:
    job.status = UploadJob.Status.ANONYMIZED
    job.save()
    register_upload_job_file(job.pk, Path(job.file.path), role=role)
    with pytest.raises(ValueError, match="inventory does not permit deletion"):
        recorded_upload_sources([job], digest=job.content_hash)
    assert Path(job.file.path).exists()
