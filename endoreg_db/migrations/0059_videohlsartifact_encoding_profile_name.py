from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0058_medicalledgerwritereceipt")]

    operations = [
        migrations.AddField(
            model_name="videohlsartifact",
            name="encoding_profile_name",
            field=models.CharField(
                default="clinical_h264_libx264_crf_v1",
                max_length=64,
            ),
        ),
    ]
