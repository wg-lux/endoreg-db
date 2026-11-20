from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0092_video_frame_provenance"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StudyTableImport",
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
                ("mapping_id", models.UUIDField()),
                ("source_id", models.UUIDField()),
                ("mapping_hash", models.CharField(max_length=64)),
                ("source_hash", models.CharField(max_length=64)),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "center",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.center",
                    ),
                ),
                (
                    "patient_examination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.patientexamination",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("owner", "center", "mapping_id", "source_id"),
                        name="unique_study_table_import_row",
                    )
                ]
            },
        )
    ]
