from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "endoreg_db",
            "0018_remove_uploadjob_uniq_uploadjob_content_hash_per_center_type_and_more",
        ),
    ]

    operations = [
        migrations.AlterField(
            model_name="aidataset",
            name="dataset_type",
            field=models.CharField(
                choices=[("image", "Image"), ("video", "Video")],
                default="image",
                help_text=(
                    "Primary annotation modality used for training. Export helpers may "
                    "still include both frame and video annotations attached to the dataset."
                ),
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="aidataset",
            name="video_annotations",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Video-segment annotations collected from the video examination "
                    "annotation workflow."
                ),
                related_name="video_ai_datasets",
                to="endoreg_db.labelvideosegment",
            ),
        ),
    ]
