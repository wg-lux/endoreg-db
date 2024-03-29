# Generated by Django 4.2.11 on 2024-03-23 15:39

import django.core.files.storage
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import pathlib


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0012_rawvideofile_prediction_dir_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='RawPdfFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(storage=django.core.files.storage.FileSystemStorage(location=pathlib.PurePosixPath('/mnt/hdd-sensitive/Pseudo/import/report')), upload_to='raw_pdf/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf'])])),
                ('pdf_hash', models.CharField(max_length=255, unique=True)),
                ('report_processed', models.BooleanField(default=False)),
                ('text', models.TextField(blank=True, null=True)),
                ('anonymized_text', models.TextField(blank=True, null=True)),
                ('raw_meta', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sensitive_meta', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='raw_pdf_file', to='endoreg_db.sensitivemeta')),
            ],
        ),
    ]
