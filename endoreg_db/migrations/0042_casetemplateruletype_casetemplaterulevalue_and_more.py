# Generated by Django 4.2.11 on 2024-06-09 11:02

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0041_gender_patientmedication_medication_indication_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CaseTemplateRuleType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='CaseTemplateRuleValue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('fk_value', models.CharField(blank=True, max_length=255, null=True)),
                ('numeric_value', models.FloatField(blank=True, null=True)),
                ('text_value', models.CharField(blank=True, max_length=255, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='CaseTemplateRuleValueType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='CaseTemplateType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='CaseTemplateRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('name_de', models.CharField(max_length=255, null=True)),
                ('name_en', models.CharField(max_length=255, null=True)),
                ('description', models.TextField(blank=True, null=True)),
                ('value_type', models.CharField(blank=True, max_length=255, null=True)),
                ('chained_rules', models.ManyToManyField(related_name='calling_rules', to='endoreg_db.casetemplaterule')),
                ('rule_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='endoreg_db.casetemplateruletype')),
                ('rule_values', models.ManyToManyField(related_name='rules', to='endoreg_db.casetemplaterulevalue')),
            ],
        ),
        migrations.CreateModel(
            name='CaseTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('case_template_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='case_templates', to='endoreg_db.casetemplatetype')),
                ('rules', models.ManyToManyField(to='endoreg_db.casetemplaterule')),
                ('secondary_rules', models.ManyToManyField(related_name='secondary_rules', to='endoreg_db.casetemplaterule')),
            ],
        ),
    ]
