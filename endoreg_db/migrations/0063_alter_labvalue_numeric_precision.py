# Generated by Django 5.0.4 on 2024-06-20 17:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0062_labvalue_numeric_precision'),
    ]

    operations = [
        migrations.AlterField(
            model_name='labvalue',
            name='numeric_precision',
            field=models.IntegerField(default=3),
        ),
    ]
