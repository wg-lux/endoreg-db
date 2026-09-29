from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0086_upload_job_current_error_state")]

    # Historical examination state cannot be reconstructed from its current value.
    operations = [
        migrations.AddField(
            model_name="patientexaminationreport",
            name="dtypes_record",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="patientexaminationreport",
            name="dtypes_record_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
