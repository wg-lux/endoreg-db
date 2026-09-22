from __future__ import annotations

import uuid
from django.db import migrations, models


def backfill_terminal_error_codes(apps, schema_editor) -> None:
    upload_job = apps.get_model("endoreg_db", "UploadJob")
    upload_job.objects.filter(status="error", error_code="").update(
        error_code="processing_failed"
    )
    upload_job.objects.filter(status="lost", error_code="").update(
        error_code="source_missing"
    )
    video_hls_artifact = apps.get_model("endoreg_db", "VideoHlsArtifact")
    video_hls_artifact.objects.filter(status="failed", error_code="").update(
        error_code="materialization_failed"
    )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0048_labelvideosegment_source_identity")]

    operations = [
        migrations.AlterField(
            model_name="uploadjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("retrying", "Retrying"),
                    ("anonymized", "Anonymized"),
                    ("error", "Error"),
                    ("lost", "Lost"),
                ],
                default="pending",
                help_text="Current processing status of the upload",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="videohlsartifact",
            name="error_code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("dispatch_failed", "Dispatch Failed"),
                    ("inconsistent_artifact", "Inconsistent Artifact"),
                    ("materialization_failed", "Materialization Failed"),
                    ("stale_attempt", "Stale Attempt"),
                ],
                default="",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="videohlsartifact",
            name="source_generation_id",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text="Opaque generation identifier for the source snapshot of this HLS attempt.",
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="error_code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("dispatch_unavailable", "Dispatch Unavailable"),
                    ("duplicate_content", "Duplicate Content"),
                    ("invalid_configuration", "Invalid Configuration"),
                    ("invalid_input", "Invalid Input"),
                    ("media_integrity_failed", "Media Integrity Failed"),
                    ("processing_failed", "Processing Failed"),
                    ("source_missing", "Source Missing"),
                ],
                default="",
                help_text="Stable machine-readable import failure classification.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="last_attempt_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When import processing was most recently attempted.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="max_retries",
            field=models.PositiveIntegerField(
                default=3,
                help_text="Maximum number of automatic retries allowed for this job.",
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="next_retry_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the next automatic retry becomes due.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="retry_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of automatic retries scheduled for this job.",
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="retryable",
            field=models.BooleanField(
                default=False,
                help_text="Whether this job is waiting for an automatic retry.",
            ),
        ),
        migrations.RunPython(backfill_terminal_error_codes, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="uploadjob",
            index=models.Index(
                fields=["status", "next_retry_at"],
                name="upload_job_retry_due_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="videohlsartifact",
            constraint=models.CheckConstraint(
                condition=(models.Q(status="failed") & ~models.Q(error_code=""))
                | (~models.Q(status="failed") & models.Q(error_code="")),
                name="video_hls_failure_coded",
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="retrying",
                        retryable=True,
                        next_retry_at__isnull=False,
                        retry_count__gt=0,
                    )
                    & ~models.Q(error_code="")
                )
                | (
                    ~models.Q(status="retrying")
                    & models.Q(retryable=False, next_retry_at__isnull=True)
                ),
                name="upload_job_retry_state_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadjob",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=["error", "lost"]) | ~models.Q(error_code="")
                ),
                name="upload_job_terminal_error_coded",
            ),
        ),
    ]
