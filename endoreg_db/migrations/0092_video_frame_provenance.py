from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0091_upload_job_file")]

    operations = [
        migrations.CreateModel(
            name="VideoFrameProvenance",
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
                ("source_content_hash", models.CharField(max_length=64, db_index=True)),
                ("output_content_hash", models.CharField(max_length=64, db_index=True)),
                ("payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("source_content_hash", "output_content_hash"),
                        name="unique_video_frame_provenance_edge",
                    )
                ]
            },
        ),
    ]
