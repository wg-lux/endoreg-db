# Generated by Django 4.2.11 on 2024-04-29 07:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0031_rename_adapt_to_liver_function_medication_adapt_to_age_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='medicationschedule',
            name='therapy_duration_d',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
