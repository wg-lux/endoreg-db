from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0005_rawpdffile_uuid"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApplicationSettings",
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
                    "annotator_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "report_template_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "center",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="+",
                        to="endoreg_db.center",
                    ),
                ),
                (
                    "processor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="+",
                        to="endoreg_db.endoscopyprocessor",
                    ),
                ),
            ],
            options={
                "verbose_name": "Application Settings",
                "verbose_name_plural": "Application Settings",
            },
        ),
    ]
