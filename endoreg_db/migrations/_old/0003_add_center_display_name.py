from django.db import migrations, models


def populate_display_name(apps, schema_editor):
    Center = apps.get_model('endoreg_db', 'Center')
    for center in Center.objects.all():
        if not center.display_name:
            center.display_name = center.name
            center.save(update_fields=['display_name'])


def reset_display_name(apps, schema_editor):
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
