# Generated by Django 5.0.4 on 2024-06-24 15:49

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0066_alter_patientlabvalue_patient_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='medicationindication',
            name='indication_type',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medication_indications', to='endoreg_db.medicationindicationtype'),
        ),
    ]
