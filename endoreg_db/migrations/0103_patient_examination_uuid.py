from uuid import uuid4

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def populate_uuids(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    for model_name in ("Patient", "PatientExamination"):
        model = apps.get_model("endoreg_db", model_name)
        rows = model.objects.using(schema_editor.connection.alias)
        for pk in (
            rows.filter(uuid__isnull=True).values_list("pk", flat=True).iterator()
        ):
            rows.filter(pk=pk, uuid__isnull=True).update(uuid=uuid4())


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0102_patient_hash_alias")]

    operations = (
        [
            migrations.AddField(
                model_name=name,
                name="uuid",
                field=models.UUIDField(null=True, editable=False),
            )
            for name in ("patient", "patientexamination")
        ]
        + [
            migrations.RunPython(populate_uuids, migrations.RunPython.noop),
        ]
        + [
            migrations.AlterField(
                model_name=name,
                name="uuid",
                field=models.UUIDField(default=uuid4, unique=True, editable=False),
            )
            for name in ("patient", "patientexamination")
        ]
    )
