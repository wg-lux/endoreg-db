import uuid

from django.db import migrations, models


def populate_rawpdffile_uuid(apps, schema_editor):
    raw_pdf_model = apps.get_model("endoreg_db", "RawPdfFile")

    for raw_pdf in raw_pdf_model.objects.filter(uuid__isnull=True).iterator():
        raw_pdf.uuid = uuid.uuid4()
        raw_pdf.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0004_videofile_uuid"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawpdffile",
            name="uuid",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(
            populate_rawpdffile_uuid, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="rawpdffile",
            name="uuid",
            field=models.UUIDField(
                default=uuid.uuid4, unique=True, editable=False
            ),
        ),
    ]

