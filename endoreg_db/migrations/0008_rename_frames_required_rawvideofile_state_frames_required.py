# Generated by Django 4.2.11 on 2024-03-10 15:45

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0007_rawvideofile_processor'),
    ]

    operations = [
        migrations.RenameField(
            model_name='rawvideofile',
            old_name='frames_required',
            new_name='state_frames_required',
        ),
    ]