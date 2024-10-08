# Generated by Django 4.2.15 on 2024-09-17 11:46

import django.core.files.storage
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0004_alter_rawpdffile_file'),
    ]

    operations = [
        migrations.CreateModel(
            name='UploadedFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('original_file', models.FileField(upload_to='uploads/original/')),
                ('upload_date', models.DateTimeField(default=django.utils.timezone.now)),
                ('description', models.TextField(blank=True, null=True)),
            ],
        ),
        migrations.AlterField(
            model_name='rawpdffile',
            name='file',
            field=models.FileField(storage=django.core.files.storage.FileSystemStorage(location='/mnt/hdd-sensitive/Pseudo/import/pdf'), upload_to='raw_pdf/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf'])]),
        ),
        migrations.CreateModel(
            name='AnonymizedFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anonymized_file', models.FileField(upload_to='uploads/anonymized/')),
                ('anonymization_date', models.DateTimeField(default=django.utils.timezone.now)),
                ('original_file', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='anonymized_file', to='endoreg_db.uploadedfile')),
            ],
        ),
    ]
