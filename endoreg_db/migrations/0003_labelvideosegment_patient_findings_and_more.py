# Generated by Django 5.2 on 2025-04-15 17:31

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0002_alter_rawvideofile_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='labelvideosegment',
            name='patient_findings',
            field=models.ManyToManyField(blank=True, related_name='video_segments', to='endoreg_db.patientfinding'),
        ),
        migrations.AlterField(
            model_name='sensitivemeta',
            name='patient_dob',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
