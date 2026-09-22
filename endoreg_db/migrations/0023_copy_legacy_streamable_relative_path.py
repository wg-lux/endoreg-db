from django.db import migrations


def copy_legacy_streamable_relative_path(apps, schema_editor):
    VideoFile = apps.get_model("endoreg_db", "VideoFile")
    table_name = VideoFile._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

        required_columns = {
            "streamable_relative_path",
            "raw_streamable_relative_path",
            "storage_mode",
        }
        if not required_columns.issubset(existing_columns):
            return

        quote_name = schema_editor.quote_name
        table = quote_name(table_name)
        legacy_column = quote_name("streamable_relative_path")
        raw_column = quote_name("raw_streamable_relative_path")
        storage_mode_column = quote_name("storage_mode")

        cursor.execute(
            (
                f"UPDATE {table} "
                f"SET {raw_column} = {legacy_column} "
                f"WHERE {legacy_column} IS NOT NULL "
                f"AND {legacy_column} <> '' "
                f"AND ({raw_column} IS NULL OR {raw_column} = '')"
            )
        )
        cursor.execute(
            (
                f"UPDATE {table} "
                f"SET {storage_mode_column} = 'fs_encrypted_streamable' "
                f"WHERE {legacy_column} IS NOT NULL "
                f"AND {legacy_column} <> '' "
                f"AND ({storage_mode_column} IS NULL "
                f"OR {storage_mode_column} = '' "
                f"OR {storage_mode_column} = 'app_encrypted')"
            )
        )


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0022_repair_videofile_streamable_columns"),
    ]

    operations = [
        migrations.RunPython(
            copy_legacy_streamable_relative_path,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
