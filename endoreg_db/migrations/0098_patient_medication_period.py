from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0097_application_persistence")]

    operations = [
        migrations.AddField(
            model_name="patientmedication",
            name="start_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="patientmedication",
            name="end_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="patientmedication",
            constraint=models.CheckConstraint(
                condition=models.Q(end_at__isnull=True)
                | (
                    models.Q(start_at__isnull=False)
                    & models.Q(end_at__gte=models.F("start_at"))
                ),
                name="patient_medication_valid_period",
            ),
        ),
    ]
