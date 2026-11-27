import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0104_patient_matching")]
    operations = [
        migrations.CreateModel(
            name="StudyCollectionEntry",
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
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("payload", models.JSONField()),
                (
                    "package_import",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="study_collection_entries",
                        to="endoreg_db.clinicalreferenceimport",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "study",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_entries",
                        to="endoreg_db.study",
                    ),
                ),
                (
                    "cohort",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_entries",
                        to="endoreg_db.studycohort",
                    ),
                ),
                (
                    "patient_examination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="study_collection_entries",
                        to="endoreg_db.patientexamination",
                    ),
                ),
            ],
        ),
    ]
