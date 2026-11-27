from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0117_center_shift_presence_fraction")]

    operations = [
        migrations.AddField(
            model_name="outboundhubtransferjob",
            name=name,
            field=models.DateTimeField(blank=True, null=True),
        )
        for name in (
            "local_files_cleanup_started_at",
            "local_files_cleanup_completed_at",
        )
    ]
