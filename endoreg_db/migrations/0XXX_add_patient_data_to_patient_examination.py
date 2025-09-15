# Generated migration for patient examination fields
# 🎯 Purpose: Add patient_birth_date and patient_gender fields to PatientExamination
# for accurate age calculation during requirement evaluation

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0XXX_previous_migration'),  # Replace with actual previous migration
    ]

    operations = [
        migrations.AddField(
            model_name='patientexamination',
            name='patient_birth_date',
            field=models.DateField(blank=True, help_text="Patient's birth date at time of examination", null=True),
        ),
        migrations.AddField(
            model_name='patientexamination',
            name='patient_gender',
            field=models.CharField(blank=True, help_text="Patient's gender at time of examination", max_length=10, null=True),
        ),
    ]
