# Generated by Django 5.0.4 on 2024-06-24 10:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0064_casetemplaterule_extra_parameters_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='casetemplaterule',
            old_name='_date_value_distribution',
            new_name='date_value_distribution',
        ),
        migrations.RenameField(
            model_name='casetemplaterule',
            old_name='_multiple_categorical_value_distribution',
            new_name='multiple_categorical_value_distribution',
        ),
        migrations.RenameField(
            model_name='casetemplaterule',
            old_name='_numerical_value_distribution',
            new_name='numerical_value_distribution',
        ),
        migrations.RenameField(
            model_name='casetemplaterule',
            old_name='_single_categorical_value_distribution',
            new_name='single_categorical_value_distribution',
        ),
        migrations.RenameField(
            model_name='labvalue',
            old_name='_default_date_value_distribution',
            new_name='default_date_value_distribution',
        ),
        migrations.RenameField(
            model_name='labvalue',
            old_name='_default_multiple_categorical_value_distribution',
            new_name='default_multiple_categorical_value_distribution',
        ),
        migrations.RenameField(
            model_name='labvalue',
            old_name='_default_numerical_value_distribution',
            new_name='default_numerical_value_distribution',
        ),
        migrations.RenameField(
            model_name='labvalue',
            old_name='_default_single_categorical_value_distribution',
            new_name='default_single_categorical_value_distribution',
        ),
        migrations.AlterField(
            model_name='casetemplate',
            name='name',
            field=models.CharField(max_length=255, unique=True),
        ),
    ]
