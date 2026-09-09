from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0072_patient_examination_report_provenance")]

    operations = [
        migrations.AlterField(
            model_name="uploadjob",
            name="cleanup_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("eligible", "Eligible"),
                    ("deleting", "Deleting"),
                    ("completed", "Completed"),
                    ("skipped", "Skipped"),
                ],
                default="pending",
                help_text="Cleanup state for the persisted source artifact.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_receipt_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                help_text="Stable authorization receipt for a source cleanup attempt.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Database time when the current source cleanup was authorized.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Database time when source cleanup reconciliation completed.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_fencing_token",
            field=models.PositiveBigIntegerField(
                blank=True,
                editable=False,
                help_text="Import fencing token captured by the cleanup authorization.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_source_name_sha256",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Opaque storage-name identity captured before source cleanup.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_source_size_bytes",
            field=models.PositiveBigIntegerField(
                blank=True,
                editable=False,
                help_text="Source object size captured before source cleanup.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_source_content_sha256",
            field=models.CharField(
                blank=True,
                default="",
                editable=False,
                help_text="Plaintext source digest captured before source cleanup.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_failure_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Number of failed source cleanup mutations or reconciliations.",
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cleanup_last_error_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Stable non-sensitive classification of the last cleanup failure.",
                max_length=64,
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        cleanup_status="deleting",
                        cleanup_receipt_id__isnull=False,
                        cleanup_started_at__isnull=False,
                        cleanup_fencing_token__isnull=False,
                        cleanup_source_name_sha256__gt="",
                        cleanup_source_size_bytes__isnull=False,
                        cleanup_source_content_sha256__gt="",
                    )
                    | ~models.Q(cleanup_status="deleting")
                ),
                name="upload_job_cleanup_receipt_required",
            ),
        ),
    ]
