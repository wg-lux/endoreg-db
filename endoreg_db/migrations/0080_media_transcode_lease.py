from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0079_upload_job_cancellation")]

    operations = [
        migrations.AlterField(
            model_name="mediaoperationlease",
            name="lease_type",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("stream", "Active stream"),
                    ("segment_update", "Segment update"),
                    ("transcode", "Exclusive video transcode"),
                    ("artifact_write", "Exclusive video artifact write"),
                ],
            ),
        ),
    ]
