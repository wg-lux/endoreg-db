import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0090_reference_catalog_import")]

    operations = [
        migrations.CreateModel(
            name="UploadJobFile",
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
                ("path", models.CharField(max_length=2048, db_index=True)),
                (
                    "role",
                    models.CharField(
                        max_length=16,
                        choices=[
                            ("source", "Import source"),
                            ("sidecar", "Import sidecar"),
                            ("working", "Disposable working file"),
                            ("retained", "Retained media artifact"),
                            ("quarantine", "Quarantined file"),
                        ],
                    ),
                ),
                ("size_bytes", models.PositiveBigIntegerField(null=True)),
                ("is_directory", models.BooleanField(default=False)),
                ("removed_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "upload_job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="files",
                        to="endoreg_db.uploadjob",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("upload_job", "path"),
                        name="unique_upload_job_file_path",
                    )
                ]
            },
        ),
    ]
