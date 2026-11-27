from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0019_aidataset_video_annotations"),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationsettings",
            name="ai_dataset_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="applicationsettings",
            name="ai_dataset_type",
            field=models.CharField(
                blank=True,
                choices=[("", "Unset"), ("image", "Image"), ("video", "Video")],
                default="",
                max_length=32,
            ),
        ),
    ]
