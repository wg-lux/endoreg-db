# Generated by Django 5.0.4 on 2024-06-24 09:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0063_alter_labvalue_numeric_precision'),
    ]

    operations = [
        migrations.AddField(
            model_name='casetemplaterule',
            name='extra_parameters',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='casetemplaterule',
            name='parent_field',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
