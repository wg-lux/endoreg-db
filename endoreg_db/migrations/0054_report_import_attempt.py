from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0053_upload_job_import_lease")]

    operations = [
        migrations.CreateModel(
            name="ReportImportAttempt",
            fields=[
                (
                    "content_hash",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                (
                    "fencing_token",
                    models.PositiveBigIntegerField(default=0),
                ),
                (
                    "owner_id",
                    models.UUIDField(blank=True, null=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("idle", "Idle"),
                            ("active", "Active"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="idle",
                        max_length=16,
                    ),
                ),
                (
                    "heartbeat_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "lease_expires_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
            ],
            options={
                "db_table": "report_import_attempt",
            },
        ),
        migrations.AddConstraint(
            model_name="reportimportattempt",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="active",
                        owner_id__isnull=False,
                        heartbeat_at__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | (
                        ~models.Q(status="active")
                        & models.Q(
                            owner_id__isnull=True,
                            heartbeat_at__isnull=True,
                            lease_expires_at__isnull=True,
                        )
                    )
                ),
                name="report_attempt_lease_state_consistent",
            ),
        ),
    ]
