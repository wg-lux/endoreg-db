# Generated by Django 4.2.11 on 2024-06-09 11:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0043_casetemplatetype_name_de_casetemplatetype_name_en'),
    ]

    operations = [
        migrations.AddField(
            model_name='casetemplateruletype',
            name='name_de',
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='casetemplateruletype',
            name='name_en',
            field=models.CharField(max_length=255, null=True),
        ),
    ]