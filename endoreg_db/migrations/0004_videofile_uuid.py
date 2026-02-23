import uuid

from django.db import migrations, models


def populate_videofile_uuid(apps, schema_editor):
    video_file_model = apps.get_model("endoreg_db", "VideoFile")

    for video in video_file_model.objects.filter(uuid__isnull=True).iterator():
        video.uuid = uuid.uuid4()
        video.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0003_patientexaminationreport_report_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="videofile",
            name="uuid",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(
            populate_videofile_uuid, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="videofile",
            name="uuid",
            field=models.UUIDField(
                default=uuid.uuid4, unique=True, editable=False
            ),
        ),
    ]

