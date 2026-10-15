from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0089_unit_description_text")]

    operations = [
        migrations.CreateModel(
            name="ReferenceCatalogImport",
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
                ("projection", models.CharField(max_length=2048, default="all")),
                ("snapshot_id", models.CharField(max_length=71)),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("module", "version", "projection"),
                        name="unique_reference_catalog_import",
                    )
                ],
            },
        ),
    ]
