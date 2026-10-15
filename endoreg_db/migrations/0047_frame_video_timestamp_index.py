from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0046_dicom_interoperability"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="frame",
            index=models.Index(
                fields=["video", "timestamp"],
                name="frame_video_timestamp_idx",
            ),
        ),
    ]
