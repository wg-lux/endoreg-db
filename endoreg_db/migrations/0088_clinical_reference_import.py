from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0087_report_dtypes_snapshot")]

    operations = [
        migrations.CreateModel(
            name="ClinicalReferenceImport",
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
                ("module", models.CharField(max_length=255)),
                ("version", models.CharField(max_length=100)),
                ("snapshot_id", models.CharField(max_length=71)),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("module", "version"),
                        name="unique_clinical_reference_import",
                    )
                ],
            },
        ),
    ]
