"""Direct callers acquire real UploadJob ownership before expensive processing."""

from pathlib import Path

import pytest
from django.db import OperationalError, transaction

from endoreg_db.models import Center, UploadJob, VideoFile
from endoreg_db.services.video_files import direct_import as sut
from endoreg_db.services.hub.upload_job_import_lease import UploadJobImportLeaseLost
from endoreg_db.services.imports.execution import ImportExecutionFence


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["success", "failure", "outage", "lost"])
def test_direct_import_owns_processing_and_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    source = tmp_path / "direct.mp4"
    source.write_bytes(b"test source bytes")
    center = Center.objects.create(name="direct-center", center_key="direct-center")
    service = sut.VideoImportService()
    errors: dict[str, Exception] = {
        "failure": ValueError("processing failed"),
        "outage": OperationalError("database connection unavailable"),
        "lost": UploadJobImportLeaseLost("ownership lost"),
    }

    def process(
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        *,
        execution_fence: ImportExecutionFence,
        retry: bool = False,
    ) -> VideoFile:
        assert transaction.get_autocommit()
        assert Path(file_path).read_bytes() == source.read_bytes()
        assert center_name == center.name
        assert processor_name == "processor"
        assert retry is False
        job = UploadJob.objects.get(source_system="direct_video_import")
        assert job.status == UploadJob.Status.PROCESSING
        assert job.processing_lease_owner == f"execution-{execution_fence.attempt_id}"
        execution_fence.guard()
        with execution_fence.mutation_guard():
            assert not transaction.get_autocommit()
        if outcome in errors:
            raise errors[outcome]
        return VideoFile(center=center)

    monkeypatch.setattr(service, "import_and_anonymize_fenced", process)
    if outcome == "success":
        service.import_and_anonymize(source, center.name, "processor")
    else:
        with pytest.raises(type(errors[outcome])) as caught:
            service.import_and_anonymize(source, center.name, "processor")
        assert caught.value is errors[outcome]
    job = UploadJob.objects.get(source_system="direct_video_import")
    assert source.read_bytes() == b"test source bytes"
    assert job.file.name
    if outcome in {"outage", "lost"}:
        assert job.processing_lease_owner
        assert job.status == UploadJob.Status.PROCESSING
    else:
        assert not job.processing_lease_owner
        expected = (
            UploadJob.Status.ANONYMIZED
            if outcome == "success"
            else UploadJob.Status.ERROR
        )
        assert job.status == expected


@pytest.mark.django_db
def test_direct_import_rejects_outer_transaction_before_admission(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="autocommit"):
        sut.VideoImportService().import_and_anonymize(
            tmp_path / "absent.mp4", "center", "processor"
        )
    assert not UploadJob.objects.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("fail_preparation", [False, True])
def test_reimport_preparation_is_owned_and_transactional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_preparation: bool
) -> None:
    source = tmp_path / "reimport.mp4"
    source.write_bytes(b"test reimport bytes")
    center = Center.objects.create(name="before", center_key="reimport")
    video = VideoFile(center=center)
    service = sut.VideoImportService()

    def context_names(_video: VideoFile) -> tuple[str, str]:
        return "before", "processor"

    monkeypatch.setattr(sut, "get_video_import_context_names", context_names)
    events: list[str] = []

    def prepare() -> None:
        assert not transaction.get_autocommit()
        job = UploadJob.objects.get(source_system="direct_video_import")
        assert job.processing_lease_owner
        Center.objects.filter(pk=center.pk).update(display_name="prepared")
        events.append("prepare")
        if fail_preparation:
            raise ValueError("preparation failed")

    def process(
        target: VideoFile,
        *,
        execution_fence: ImportExecutionFence,
        source_path: Path | str | None = None,
    ) -> VideoFile:
        assert transaction.get_autocommit()
        assert source_path is not None
        assert Path(source_path).read_bytes() == source.read_bytes()
        execution_fence.guard()
        center.refresh_from_db()
        assert center.display_name == "prepared"
        events.append("process")
        return target

    original_display_name = center.display_name
    monkeypatch.setattr(service, "reanonymize_existing_video_fenced", process)
    if fail_preparation:
        with pytest.raises(ValueError, match="preparation failed"):
            service.reanonymize_existing_video(
                video, source_path=source, prepare=prepare
            )
        center.refresh_from_db()
        assert center.display_name == original_display_name
        assert events == ["prepare"]
    else:
        assert (
            service.reanonymize_existing_video(
                video, source_path=source, prepare=prepare
            )
            is video
        )
        assert events == ["prepare", "process"]


@pytest.mark.django_db(transaction=True)
def test_direct_import_preserves_implicit_center_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.models.administration.app_settings import ApplicationSettings

    source = tmp_path / "local.mp4"
    source.write_bytes(b"local source")
    Center.objects.create(name="Shared name", center_key="other")
    selected = Center.objects.create(name="Shared name", center_key="selected")
    ApplicationSettings.objects.update_or_create(pk=1, defaults={"center": selected})
    service = sut.VideoImportService()

    def process(
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        *,
        execution_fence: ImportExecutionFence,
        retry: bool = False,
    ) -> VideoFile:
        # Passing a resolved display name would lose the stable key when
        # several centers share that name. The core resolves this blank input.
        assert center_name == ""
        assert (
            UploadJob.objects.get(source_system="direct_video_import").source_center
            == selected
        )
        execution_fence.guard()
        return VideoFile(center=selected)

    monkeypatch.setattr(service, "import_and_anonymize_fenced", process)
    service.import_and_anonymize(source, "", "processor")
