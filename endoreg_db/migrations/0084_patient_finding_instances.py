"""Retain finding primary keys and relations while allowing repeated lesions.

Reverse migration refuses to merge multiple active findings. Operators must
resolve them explicitly before downgrading to the singleton schema.
"""

from uuid import uuid4

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def populate_instance_ids(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    findings = apps.get_model("endoreg_db", "PatientFinding").objects.using(
        schema_editor.connection.alias
    )
    for pk in (
        findings.filter(instance_id__isnull=True)
        .values_list("pk", flat=True)
        .iterator()
    ):
        findings.filter(pk=pk).update(instance_id=uuid4())


def guard_singleton_downgrade(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    repeated = (
        apps.get_model("endoreg_db", "PatientFinding")
        .objects.using(schema_editor.connection.alias)
        .filter(is_active=True)
        .values("patient_examination_id", "finding_id")
        .annotate(instance_count=models.Count("pk"))
        .filter(instance_count__gt=1)
    )
    if repeated.exists():
        raise RuntimeError(
            "Cannot downgrade repeated patient findings to singleton findings."
        )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0083_repair_raw_video_hash_column")]

    operations = [
        migrations.AddField(
            model_name="patientfinding",
            name="instance_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_instance_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="patientfinding",
            name="instance_id",
            field=models.UUIDField(default=uuid4, unique=True, editable=False),
        ),
        migrations.RemoveConstraint(
            model_name="patientfinding", name="unique_active_finding_per_examination"
        ),
        migrations.RunPython(migrations.RunPython.noop, guard_singleton_downgrade),
    ]
