import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0036_anonymization_quality_evaluation"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportLlmInferenceJob",
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
                    "job_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "operation",
                    models.CharField(
                        choices=[
                            ("report_llm_reimport", "Report LLM Reimport"),
                            ("report_llm_import", "Report LLM Import"),
                        ],
                        max_length=64,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failure", "Failure"),
                            ("lost", "Lost"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("task_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("queue", models.CharField(max_length=64)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "pdf",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="llm_inference_jobs",
                        to="endoreg_db.rawpdffile",
                    ),
                ),
                (
                    "upload_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="report_llm_inference_jobs",
                        to="endoreg_db.uploadjob",
                    ),
                ),
            ],
            options={
                "db_table": "report_llm_inference_job",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["pdf", "operation", "status"],
                        name="report_llm_pdf_oper_1bf524_idx",
                    ),
                    models.Index(
                        fields=["upload_job", "operation", "status"],
                        name="report_llm_upload__a8ad25_idx",
                    ),
                    models.Index(
                        fields=["queue", "status"],
                        name="report_llm_queue_3c9126_idx",
                    ),
                    models.Index(
                        fields=["status", "-created_at"],
                        name="report_llm_status_d09b28_idx",
                    ),
                ],
            },
        ),
    ]
