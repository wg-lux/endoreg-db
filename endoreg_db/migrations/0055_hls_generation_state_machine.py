from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0054_report_import_attempt")]

    operations = [
        migrations.RemoveConstraint(
            model_name="videohlsartifact",
            name="unique_video_hls_artifact_kind",
        ),
        migrations.AlterField(
            model_name="videohlsartifact",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("materializing", "Materializing"),
                    ("validated", "Validated"),
                    ("ready", "Ready"),
                    ("superseded", "Superseded"),
                    ("failed", "Failed"),
                ],
                default="materializing",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="videohlsartifact",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="ready"),
                fields=("video", "artifact_kind"),
                name="unique_ready_video_hls_artifact_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="videohlsartifact",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["queued", "materializing", "validated"]),
                fields=("video", "artifact_kind"),
                name="unique_active_video_hls_attempt",
            ),
        ),
    ]
