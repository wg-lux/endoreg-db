from django.db import migrations, models


def populate_display_name(apps, schema_editor):
    """
    Populate Center.display_name from name for centers that currently have no display name.
    
    For each Center instance in the database whose display_name is empty, set display_name to the instance's name and persist only the display_name field.
    """
    Center = apps.get_model('endoreg_db', 'Center')
    for center in Center.objects.all():
        if not center.display_name:
            center.display_name = center.name
            center.save(update_fields=['display_name'])


def reset_display_name(apps, schema_editor):
    """
    Reset the display_name field to an empty string for every Center record.
    
    This migration helper sets each Center.display_name to '' (empty string) across all Center instances.
    """
    Center = apps.get_model('endoreg_db', 'Center')
    Center.objects.update(display_name='')


class Migration(migrations.Migration):

    dependencies = [
        ('endoreg_db', '0002_add_video_correction_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='center',
            name='display_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(populate_display_name, reset_display_name),
    ]