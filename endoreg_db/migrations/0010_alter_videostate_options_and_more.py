# Generated by Django 5.1.3 on 2025-04-25 09:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0009_videostate_frame_count'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='videostate',
            options={'verbose_name': 'Video Processing State', 'verbose_name_plural': 'Video Processing States'},
        ),
        migrations.RenameField(
            model_name='videostate',
            old_name='created_at',
            new_name='date_created',
        ),
        migrations.RenameField(
            model_name='videostate',
            old_name='updated_at',
            new_name='date_modified',
        ),
        migrations.RemoveField(
            model_name='videostate',
            name='lvs_annotated',
        ),
        migrations.RemoveField(
            model_name='videostate',
            name='sensitive_data_retrieved',
        ),
        migrations.AddField(
            model_name='videostate',
            name='frame_annotations_generated',
            field=models.BooleanField(default=False, help_text='True if frame-level annotations have been generated from segments.'),
        ),
        migrations.AddField(
            model_name='videostate',
            name='text_meta_extracted',
            field=models.BooleanField(default=False, help_text='True if text metadata (OCR) has been extracted.'),
        ),
        migrations.AddField(
            model_name='videostate',
            name='video_meta_extracted',
            field=models.BooleanField(default=False, help_text='True if VideoMeta (technical specs) has been extracted.'),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='anonymized',
            field=models.BooleanField(default=False, help_text='True if the anonymized video file has been created.'),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='frame_count',
            field=models.PositiveIntegerField(blank=True, help_text='Number of frames extracted/initialized.', null=True),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='frames_extracted',
            field=models.BooleanField(default=False, help_text='True if raw frames have been extracted to files.'),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='frames_initialized',
            field=models.BooleanField(default=False, help_text='True if Frame DB objects have been created.'),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='initial_prediction_completed',
            field=models.BooleanField(default=False, help_text='True if initial AI prediction has run.'),
        ),
        migrations.AlterField(
            model_name='videostate',
            name='lvs_created',
            field=models.BooleanField(default=False, help_text='True if LabelVideoSegments have been created from predictions.'),
        ),
    ]
