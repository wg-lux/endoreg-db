# Generated by Django 5.0.4 on 2024-06-18 19:07

import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0060_labvalue__default_date_value_distribution_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='patientlabvalue',
            name='date',
        ),
        migrations.AddField(
            model_name='patientlabvalue',
            name='datetime',
            field=models.DateTimeField(auto_now_add=True, default=datetime.datetime(2024, 6, 18, 19, 7, 56, 929033, tzinfo=datetime.timezone.utc)),
            preserve_default=False,
        ),
    ]
