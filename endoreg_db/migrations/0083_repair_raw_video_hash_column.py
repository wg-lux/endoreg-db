"""Repair legacy databases whose initial migration still created video_hash.

The canonical initial migration already calls this field raw_video_hash, so
the migration state must stay unchanged. Renaming the physical column preserves
existing hashes, indexes and constraints; no hashes are recalculated.
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def repair_raw_video_hash_column(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    table = apps.get_model("endoreg_db", "VideoFile")._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table
            )
        }
    legacy_present = "video_hash" in columns
    canonical_present = "raw_video_hash" in columns
    if canonical_present and not legacy_present:
        return
    if legacy_present and not canonical_present:
        quote = schema_editor.quote_name
        schema_editor.execute(
            f"ALTER TABLE {quote(table)} "
            f"RENAME COLUMN {quote('video_hash')} TO {quote('raw_video_hash')}"
        )
        return
    raise RuntimeError(
        f"Cannot repair {table}: expected exactly one of video_hash and "
        "raw_video_hash. Both or neither exist; manual schema review is required."
    )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0082_examiner_identity_generation")]

    operations = [
        # Reversing this repair must retain the canonical column: the previous
        # migration state also expects raw_video_hash, including on fresh DBs.
        migrations.RunPython(repair_raw_video_hash_column, migrations.RunPython.noop),
    ]
