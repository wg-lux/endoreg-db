# Generated by Django 4.2.11 on 2024-06-09 11:59

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0048_remove_casetemplaterule_chained_rules_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='casetemplaterule',
            name='rule_values',
        ),
    ]