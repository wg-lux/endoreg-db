from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0044_rawpdfstate_processed_file_sha256")]

    operations = [
        migrations.AlterField(
            model_name="videohlsartifact",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("materializing", "Materializing"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                ],
                default="materializing",
                max_length=32,
            ),
        ),
    ]
