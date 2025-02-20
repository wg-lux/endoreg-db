# Generated by Django 5.1.3 on 2025-01-31 09:30

import django.core.files.storage
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0017_alter_rawpdffile_file_alter_rawvideofile_file'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rawpdffile',
            name='file',
            field=models.FileField(storage=django.core.files.storage.FileSystemStorage(location='/home/admin/endoreg-db-api-production/erc_data'), upload_to='raw_pdf/', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf'])]),
        ),
        migrations.AlterField(
            model_name='rawvideofile',
            name='file',
            field=models.FileField(storage=django.core.files.storage.FileSystemStorage(location='/home/admin/endoreg-db-api-production/erc_data'), upload_to='RAW_VIDEO_DIR_NAME', validators=[django.core.validators.FileExtensionValidator(allowed_extensions=['pdf'])]),
        ),
    ]
