from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="labelvideosegment",
            name="export_segment",
            field=models.BooleanField(
                default=False,
                help_text="If true, include this segment in export selection.",
            ),
        ),
        migrations.AddField(
            model_name="videofile",
            name="export_segments_by_video",
            field=models.BooleanField(
                default=False,
                help_text="If true, include all segments for this video in exports.",
            ),
        ),
    ]
