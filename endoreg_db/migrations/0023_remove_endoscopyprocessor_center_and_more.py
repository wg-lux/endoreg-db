# Generated by Django 5.1.6 on 2025-02-17 22:26

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0022_alter_rawpdffile_file_alter_rawvideofile_file'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='endoscopyprocessor',
            name='center',
        ),
        migrations.AddField(
            model_name='endoscopyprocessor',
            name='centers',
            field=models.ManyToManyField(blank=True, related_name='endoscopy_processors', to='endoreg_db.center'),
        ),
    ]
