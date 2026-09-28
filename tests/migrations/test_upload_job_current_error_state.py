from uuid import UUID

import pytest
from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_backfill_preserves_status_and_current_failure_diagnostics() -> None:
    before = [("endoreg_db", "0085_rawpdf_artifact_path_lengths")]
    after = [("endoreg_db", "0086_upload_job_current_error_state")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        old = executor.loader.project_state(before).apps.get_model(
            "endoreg_db", "UploadJob"
        )
        ids: dict[str, UUID] = {}
        for status in (
            "pending",
            "processing",
            "anonymized",
            "error",
            "lost",
            "cancelled",
        ):
            row = old.objects.create(
                status=status,
                error_code="processing_failed",
                error_detail="Attempt failed",
                content_hash=status,
                file="upload_jobs/source",
            )
            ids[status] = row.pk
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        new = executor.loader.project_state(after).apps.get_model(
            "endoreg_db", "UploadJob"
        )
        for status, pk in ids.items():
            row = new.objects.get(pk=pk)
            assert row.status == status
            if status in {"pending", "processing", "anonymized"}:
                assert row.error_code == row.error_detail == ""
            else:
                assert row.error_code == "processing_failed"
                assert row.error_detail == "Attempt failed"
        with pytest.raises(IntegrityError), transaction.atomic():
            new.objects.filter(pk=ids["anonymized"]).update(
                error_code="processing_failed"
            )
    finally:
        MigrationExecutor(connection).migrate(after)
