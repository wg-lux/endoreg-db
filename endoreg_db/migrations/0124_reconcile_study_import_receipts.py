"""Retire ambiguous dataset provenance left by examination-wide receipt attachment."""

from uuid import uuid5

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def reconcile_receipt_links(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    database = schema_editor.connection.alias
    receipt = apps.get_model("endoreg_db", "StudyTableImport")
    links = apps.get_model("endoreg_db", "AIDataSet_import_receipts").objects.using(
        database
    )
    # The old importer attached every receipt in this scope, even when the
    # file supplied only one row. A lone receipt in scope is unambiguous.
    other_rows = (
        receipt.objects.using(database)
        .filter(
            owner_id=models.OuterRef("studytableimport__owner_id"),
            center_id=models.OuterRef("studytableimport__center_id"),
            mapping_id=models.OuterRef("studytableimport__mapping_id"),
            patient_examination_id=models.OuterRef(
                "studytableimport__patient_examination_id"
            ),
        )
        .exclude(pk=models.OuterRef("studytableimport_id"))
    )
    candidates = links.filter(models.Exists(other_rows)).values_list(
        "pk",
        "aidataset__import_identity",
        "studytableimport__mapping_id",
        "studytableimport__owner_id",
        "studytableimport__center_id",
        "studytableimport__source_hash",
    )
    unproven: list[int] = []
    for (
        link_id,
        identity,
        mapping_id,
        owner,
        center,
        source_hash,
    ) in candidates.iterator(chunk_size=1000):
        if identity != uuid5(mapping_id, f"{owner}:{center}:{source_hash}"):
            unproven.append(link_id)
        if len(unproven) == 1000:
            links.filter(pk__in=unproven).delete()
            unproven.clear()
    if unproven:
        links.filter(pk__in=unproven).delete()


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0123_guarded_state_managers")]
    operations = [
        migrations.RunPython(reconcile_receipt_links, migrations.RunPython.noop)
    ]
