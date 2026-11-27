from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0106_study_transfer")]
    operations = [
        migrations.AddField(
            model_name="patientmatchingsession",
            name="execution_started",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="patientmatchingsession",
            name="worker_succeeded",
            field=models.BooleanField(default=False),
        ),
    ]
