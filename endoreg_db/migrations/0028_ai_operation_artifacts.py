import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0027_frameboxannotation"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIModelTrainingRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "run_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "dataset_name",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("dataset_type", models.CharField(blank=True, max_length=32)),
                ("ai_model_type", models.CharField(blank=True, max_length=255)),
                ("backbone_name", models.CharField(max_length=128)),
                ("feature_mode", models.CharField(max_length=64)),
                ("freeze_backbone", models.BooleanField(default=True)),
                ("epochs", models.PositiveIntegerField(default=10)),
                ("batch_size", models.PositiveIntegerField(default=32)),
                ("labelset_version", models.PositiveIntegerField(default=1)),
                ("treat_unlabeled_as_negative", models.BooleanField(default=True)),
                ("backbone_checkpoint", models.TextField(blank=True, null=True)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("command_kwargs", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("lost", "Lost"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                (
                    "server_instance_id",
                    models.CharField(blank=True, db_index=True, max_length=64),
                ),
                ("result", models.JSONField(blank=True, null=True)),
                ("artifact_paths", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("stdout", models.TextField(blank=True)),
                ("stderr", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="model_training_runs",
                        to="endoreg_db.aidataset",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="AIDataSetExportArtifact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "artifact_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "dataset_name",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("dataset_type", models.CharField(blank=True, max_length=32)),
                ("ai_model_type", models.CharField(blank=True, max_length=255)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("center_key", models.CharField(blank=True, max_length=255, null=True)),
                ("all_centers", models.BooleanField(default=False)),
                ("only_validated", models.BooleanField(default=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="running",
                        max_length=16,
                    ),
                ),
                ("output_path", models.TextField(blank=True)),
                ("download_filename", models.CharField(blank=True, max_length=255)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="export_artifacts",
                        to="endoreg_db.aidataset",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="aimodeltrainingrun",
            index=models.Index(
                fields=["status", "-created_at"],
                name="aid_train_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aimodeltrainingrun",
            index=models.Index(
                fields=["server_instance_id", "status"],
                name="aid_train_server_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aidatasetexportartifact",
            index=models.Index(
                fields=["status", "-created_at"],
                name="aid_export_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="aidatasetexportartifact",
            index=models.Index(
                fields=["dataset", "-created_at"],
                name="aid_export_dataset_idx",
            ),
        ),
    ]
