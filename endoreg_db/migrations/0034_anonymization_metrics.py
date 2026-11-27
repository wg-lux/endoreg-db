# Generated for anonymization metrics v1.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0033_media_operation_lease"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AnonymizationValidationMetric",
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
                    "schema_version",
                    models.CharField(default="1.0", editable=False, max_length=16),
                ),
                (
                    "media_type",
                    models.CharField(
                        choices=[("video", "Video"), ("pdf", "PDF")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                (
                    "validated_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                (
                    "status_before",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "status_after",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "document_type",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "source_system",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "anonymizer_source",
                    models.CharField(
                        blank=True,
                        default="lx_anonymizer",
                        max_length=255,
                    ),
                ),
                (
                    "anonymizer_version",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "no_more_names_confirmed",
                    models.BooleanField(blank=True, null=True),
                ),
                ("seconds_to_validation", models.FloatField(blank=True, null=True)),
                ("total_fields", models.PositiveIntegerField(default=0)),
                ("changed_fields", models.PositiveIntegerField(default=0)),
                ("exact_match_fields", models.PositiveIntegerField(default=0)),
                (
                    "missing_after_validation_fields",
                    models.PositiveIntegerField(default=0),
                ),
                ("mean_similarity", models.FloatField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "center",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anonymization_validation_metrics",
                        to="endoreg_db.center",
                    ),
                ),
                (
                    "pdf",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anonymization_validation_metrics",
                        to="endoreg_db.rawpdffile",
                    ),
                ),
                (
                    "sensitive_meta",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anonymization_validation_metrics",
                        to="endoreg_db.sensitivemeta",
                    ),
                ),
                (
                    "validator_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anonymization_validation_metrics",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "video",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="anonymization_validation_metrics",
                        to="endoreg_db.videofile",
                    ),
                ),
                (
                    "validator_username",
                    models.CharField(blank=True, default="", max_length=255),
                ),
            ],
            options={
                "verbose_name": "Anonymization Validation Metric",
                "verbose_name_plural": "Anonymization Validation Metrics",
                "indexes": [
                    models.Index(
                        fields=["media_type", "validated_at"],
                        name="anon_val_media_time_idx",
                    ),
                    models.Index(
                        fields=["center", "validated_at"],
                        name="anon_val_center_time_idx",
                    ),
                    models.Index(
                        fields=["document_type", "validated_at"],
                        name="anon_val_doc_time_idx",
                    ),
                    models.Index(
                        fields=["source_system", "validated_at"],
                        name="anon_val_source_time_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AnonymizationFieldMetric",
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
                    "field_name",
                    models.CharField(
                        choices=[
                            ("patient_first_name", "Patient First Name"),
                            ("patient_last_name", "Patient Last Name"),
                            ("patient_dob", "Patient Date Of Birth"),
                            ("patient_gender", "Patient Gender"),
                            ("examination_date", "Examination Date"),
                            ("casenumber", "Case Number"),
                            ("center_name", "Center Name"),
                            ("external_id", "External ID"),
                            ("document_type", "Document Type"),
                        ],
                        db_index=True,
                        max_length=64,
                    ),
                ),
                ("present_before", models.BooleanField(default=False)),
                ("present_after", models.BooleanField(default=False)),
                ("changed", models.BooleanField(default=False)),
                ("exact_match", models.BooleanField(default=False)),
                ("similarity_score", models.FloatField(blank=True, null=True)),
                ("was_required", models.BooleanField(default=False)),
                ("was_empty_after_validation", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "validation_metric",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="field_metrics",
                        to="endoreg_db.anonymizationvalidationmetric",
                    ),
                ),
            ],
            options={
                "verbose_name": "Anonymization Field Metric",
                "verbose_name_plural": "Anonymization Field Metrics",
                "indexes": [
                    models.Index(
                        fields=["field_name", "created_at"],
                        name="anon_field_name_created_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("validation_metric", "field_name"),
                        name="uniq_anonymization_field_metric_per_validation",
                    )
                ],
            },
        ),
        migrations.AddIndex(
            model_name="videofile",
            index=models.Index(
                fields=["uploaded_at"],
                name="video_file_uploaded_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="videofile",
            index=models.Index(
                fields=["center", "uploaded_at"],
                name="video_file_center_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="rawpdffile",
            index=models.Index(
                fields=["date_created"],
                name="raw_pdf_date_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="rawpdffile",
            index=models.Index(
                fields=["center", "date_created"],
                name="raw_pdf_center_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="frameboxannotation",
            index=models.Index(
                fields=["label", "date_created"],
                name="frame_box_label_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="uploadjob",
            index=models.Index(
                fields=["status", "created_at"],
                name="upload_job_status_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="uploadjob",
            index=models.Index(
                fields=["source_center", "created_at"],
                name="upload_job_center_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="uploadjob",
            index=models.Index(
                fields=["source_system", "created_at"],
                name="upload_job_source_time_idx",
            ),
        ),
    ]
