import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0107_matching_worker_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClinicalWorkflowRecord",
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
                ("kind", models.CharField(max_length=16)),
                ("record_id", models.PositiveBigIntegerField()),
                ("privacy", models.CharField(max_length=32)),
                ("annotation", models.CharField(max_length=32)),
                ("release", models.CharField(max_length=32)),
                ("version", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "clinical_workflow_record",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("kind", "record_id"),
                        name="clinical_workflow_record_unique_media",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ClinicalWorkflowTransition",
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
                ("idempotency_key", models.CharField(max_length=128)),
                ("event", models.CharField(max_length=40)),
                ("from_version", models.PositiveBigIntegerField()),
                ("from_privacy", models.CharField(max_length=32)),
                ("from_annotation", models.CharField(max_length=32)),
                ("from_release", models.CharField(max_length=32)),
                ("to_privacy", models.CharField(max_length=32)),
                ("to_annotation", models.CharField(max_length=32)),
                ("to_release", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transitions",
                        to="endoreg_db.clinicalworkflowrecord",
                    ),
                ),
            ],
            options={
                "db_table": "clinical_workflow_transition",
                "ordering": ("record_id", "from_version"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("record", "idempotency_key"),
                        name="clinical_workflow_transition_unique_key",
                    ),
                    models.UniqueConstraint(
                        fields=("record", "from_version"),
                        name="clinical_workflow_transition_unique_version",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(idempotency_key=""),
                        name="clinical_workflow_transition_key_present",
                    ),
                ],
            },
        ),
    ]
