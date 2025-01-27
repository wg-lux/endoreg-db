# Generated by Django 5.1.3 on 2024-11-27 15:36

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0002_examinationindicationclassification_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PatientExaminationIndication",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "examination_indication",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="endoreg_db.examinationindication",
                    ),
                ),
                (
                    "patient_examination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="endoreg_db.patientexamination",
                    ),
                ),
            ],
        ),
    ]
