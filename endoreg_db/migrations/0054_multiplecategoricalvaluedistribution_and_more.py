# Generated by Django 5.0.4 on 2024-06-10 19:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0053_patientlabsampletype_patientlabsample_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MultipleCategoricalValueDistribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('categories', models.JSONField()),
                ('min_count', models.IntegerField()),
                ('max_count', models.IntegerField()),
                ('count_distribution_type', models.CharField(choices=[('uniform', 'Uniform'), ('normal', 'Normal')], max_length=20)),
                ('count_mean', models.FloatField(blank=True, null=True)),
                ('count_std_dev', models.FloatField(blank=True, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='NumericValueDistribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('distribution_type', models.CharField(choices=[('uniform', 'Uniform'), ('normal', 'Normal'), ('skewed_normal', 'Skewed Normal')], max_length=20)),
                ('min_value', models.FloatField()),
                ('max_value', models.FloatField()),
                ('mean', models.FloatField(blank=True, null=True)),
                ('std_dev', models.FloatField(blank=True, null=True)),
                ('skewness', models.FloatField(blank=True, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='SingleCategoricalValueDistribution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('categories', models.JSONField()),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.RemoveField(
            model_name='casetemplaterulevalue',
            name='target_field',
        ),
        migrations.RemoveField(
            model_name='casetemplaterulevalue',
            name='value_type',
        ),
        migrations.AddField(
            model_name='casetemplaterule',
            name='target_field',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]