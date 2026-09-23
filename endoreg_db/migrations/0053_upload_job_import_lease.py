from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0052_case_anchor")]

    operations = [
        migrations.AddField(
            model_name="uploadjob",
            name="processing_fencing_token",
            field=models.PositiveBigIntegerField(
                default=0,
                help_text="Monotonic token fencing stale import workers from state changes.",
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="processing_heartbeat_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Database time of the most recent import-processing heartbeat.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="processing_lease_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Database-time expiry of the current import-processing lease.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="processing_lease_owner",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Opaque worker identity that currently owns import processing.",
                max_length=255,
            ),
        ),
        migrations.AddIndex(
            model_name="uploadjob",
            index=models.Index(
                fields=["status", "processing_lease_expires_at"],
                name="upload_job_lease_due_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        processing_lease_owner="",
                        processing_lease_expires_at__isnull=True,
                        processing_heartbeat_at__isnull=True,
                    )
                    | (
                        ~models.Q(processing_lease_owner="")
                        & models.Q(
                            processing_lease_expires_at__isnull=False,
                            processing_heartbeat_at__isnull=False,
                        )
                    )
                ),
                name="upload_job_lease_state_consistent",
            ),
        ),
    ]
