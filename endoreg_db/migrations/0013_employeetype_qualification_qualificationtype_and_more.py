# Generated by Django 5.2 on 2025-05-12 16:35

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0012_alter_anonymexaminationreport_file_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Qualification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='QualificationType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='ScheduledDays',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('working_days', models.BooleanField(blank=True, default=True, null=True)),
                ('non_working_days', models.BooleanField(blank=True, default=False, null=True)),
                ('limited_time', models.BooleanField(blank=True, default=False, null=True)),
                ('start_date', models.DateField(blank=True, null=True)),
                ('end_date', models.DateField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='ShiftType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_name', models.CharField(max_length=255)),
                ('last_name', models.CharField(max_length=255)),
                ('dob', models.DateField(blank=True, null=True, verbose_name='Date of Birth')),
                ('email', models.EmailField(blank=True, max_length=255, null=True)),
                ('phone', models.CharField(blank=True, max_length=255, null=True)),
                ('is_real_person', models.BooleanField(default=True)),
                ('gender', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='endoreg_db.gender')),
                ('employee_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='employees', to='endoreg_db.employeetype')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='employeetype',
            name='qualifications',
            field=models.ManyToManyField(related_name='employee_types', to='endoreg_db.qualification'),
        ),
        migrations.CreateModel(
            name='EmployeeQualification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('employee', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='qualification', to='endoreg_db.employee')),
                ('qualifications', models.ManyToManyField(related_name='employee_qualifications', to='endoreg_db.qualification')),
            ],
        ),
        migrations.AddField(
            model_name='qualification',
            name='qualification_types',
            field=models.ManyToManyField(related_name='qualifications', to='endoreg_db.qualificationtype'),
        ),
        migrations.CreateModel(
            name='Shift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('name_de', models.CharField(max_length=255)),
                ('name_en', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('required_qualifications', models.ManyToManyField(related_name='shifts', to='endoreg_db.qualification')),
                ('shift_types', models.ManyToManyField(related_name='shifts', to='endoreg_db.shifttype')),
            ],
        ),
        migrations.CreateModel(
            name='CenterShift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, unique=True)),
                ('name_de', models.CharField(max_length=255)),
                ('name_en', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('estimated_presence_fraction', models.DecimalField(decimal_places=4, default=0.0, max_digits=5)),
                ('center', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='center_shifts', to='endoreg_db.center')),
                ('scheduled_days', models.ManyToManyField(related_name='center_shifts', to='endoreg_db.scheduleddays')),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='center_shifts', to='endoreg_db.shift')),
            ],
        ),
    ]
