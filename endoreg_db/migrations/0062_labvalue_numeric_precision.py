# Generated by Django 5.0.4 on 2024-06-20 17:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0061_remove_patientlabvalue_date_patientlabvalue_datetime'),
    ]

    operations = [
        migrations.AddField(
            model_name='labvalue',
            name='numeric_precision',
            field=models.CharField(default='.2f', max_length=5),
        ),
    ]
