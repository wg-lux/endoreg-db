# Generated by Django 5.2 on 2025-05-26 14:32

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0028_alter_medicationintaketime_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='requirementoperator',
            name='evaluation_function_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
