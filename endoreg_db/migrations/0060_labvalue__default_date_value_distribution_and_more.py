# Generated by Django 5.0.4 on 2024-06-18 19:02

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0059_casetemplaterule_rule_values'),
    ]

    operations = [
        migrations.AddField(
            model_name='labvalue',
            name='_default_date_value_distribution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='default_date_value_distribution', to='endoreg_db.datevaluedistribution'),
        ),
        migrations.AddField(
            model_name='labvalue',
            name='_default_multiple_categorical_value_distribution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='default_multiple_categorical_value_distribution', to='endoreg_db.multiplecategoricalvaluedistribution'),
        ),
        migrations.AddField(
            model_name='labvalue',
            name='_default_numerical_value_distribution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='default_numerical_value_distribution', to='endoreg_db.numericvaluedistribution'),
        ),
        migrations.AddField(
            model_name='labvalue',
            name='_default_single_categorical_value_distribution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='default_single_categorical_value_distribution', to='endoreg_db.singlecategoricalvaluedistribution'),
        ),
        migrations.AlterField(
            model_name='patientlabvalue',
            name='patient',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lab_values', to='endoreg_db.patient'),
        ),
        migrations.AlterField(
            model_name='patientlabvalue',
            name='sample',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='values', to='endoreg_db.patientlabsample'),
        ),
    ]