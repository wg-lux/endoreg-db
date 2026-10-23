from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0049_upload_job_import_monitoring"),
    ]

    operations = [
        migrations.AddField(
            model_name="frame",
            name="presentation_timestamp",
            field=models.BigIntegerField(
                blank=True,
                help_text=(
                    "Exact presentation timestamp tick in the selected video "
                    "stream time base."
                ),
                null=True,
            ),
        ),
    ]
