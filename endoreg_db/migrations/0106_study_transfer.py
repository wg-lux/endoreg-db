"""Add standalone typed study ledger transfer receipts."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0105_study_collection_entry")]
    operations = [
        migrations.AlterField(
            model_name="transferjob",
            name="resource_kind",
            field=models.CharField(
                max_length=16,
                choices=[
                    ("video", "Video"),
                    ("report", "Report"),
                    ("study", "Study ledger"),
                ],
            ),
        )
    ]
