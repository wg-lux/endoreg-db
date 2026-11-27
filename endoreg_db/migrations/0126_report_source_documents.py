from uuid import uuid4

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def populate_report_uuids(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    reports = apps.get_model("endoreg_db", "PatientExaminationReport").objects.using(
        schema_editor.connection.alias
    )
    for pk in reports.filter(uuid__isnull=True).values_list("pk", flat=True).iterator():
        reports.filter(pk=pk).update(uuid=uuid4())


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0125_annotator_profile")]
    operations = [
        migrations.AddField(
            "patientexaminationreport",
            "uuid",
            models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_report_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            "patientexaminationreport",
            "uuid",
            models.UUIDField(default=uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            "patientexaminationreport",
            "author_academic_titles",
            models.JSONField(default=list, blank=True),
        ),
        migrations.AddField(
            "patientexaminationreport",
            "author_profession",
            models.CharField(max_length=32, default="", blank=True),
        ),
        migrations.AddField(
            "patientexaminationreport",
            "source_documents",
            models.ManyToManyField(
                "endoreg_db.RawPdfFile", related_name="annotated_reports", blank=True
            ),
        ),
    ]
