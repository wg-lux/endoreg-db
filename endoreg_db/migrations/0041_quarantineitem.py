from __future__ import annotations

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("endoreg_db", "0040_patientexamination_dtypes_record"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuarantineItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("path", models.TextField(unique=True)),
                ("relative_path", models.TextField(db_index=True)),
                ("original_filename", models.CharField(blank=True, max_length=512)),
                ("size_bytes", models.BigIntegerField(default=0)),
                ("file_mtime_ns", models.BigIntegerField(default=0)),
                ("quarantined_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_review", "Pending Review"),
                            ("retained", "Retained"),
                            ("approved_for_deletion", "Approved For Deletion"),
                            ("deleted", "Deleted"),
                            ("missing", "Missing"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending_review",
                        max_length=32,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("decision_reason", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("delete_eligible_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("error_detail", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_quarantine_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_upload_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quarantine_items",
                        to="endoreg_db.uploadjob",
                    ),
                ),
            ],
            options={
                "ordering": ["status", "-quarantined_at", "relative_path"],
                "indexes": [
                    models.Index(
                        fields=["status", "delete_eligible_at"],
                        name="quarantine_status_delete_idx",
                    ),
                    models.Index(
                        fields=["last_seen_at"],
                        name="quarantine_last_seen_idx",
                    ),
                ],
            },
        ),
    ]
