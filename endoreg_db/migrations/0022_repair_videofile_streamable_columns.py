from django.db import migrations, models


def repair_videofile_streamable_columns(apps, schema_editor):
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

    missing_fields = []
    if "raw_streamable_relative_path" not in existing_columns:
        missing_fields.append(
            models.CharField(
                name="raw_streamable_relative_path",
                max_length=512,
                blank=True,
                default="",
            )
        )
    if "processed_streamable_relative_path" not in existing_columns:
        missing_fields.append(
            models.CharField(
                name="processed_streamable_relative_path",
                max_length=512,
                blank=True,
                default="",
            )
        )
    if "storage_mode" not in existing_columns:
        missing_fields.append(
            models.CharField(
                name="storage_mode",
                max_length=64,
                default="app_encrypted",
            )
        )

    for field in missing_fields:
        field.set_attributes_from_name(field.name)
        schema_editor.add_field(VideoFile, field)


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0021_alter_aidataset_ai_model_type_and_more"),
    ]

    operations = [
        migrations.RunPython(
            repair_videofile_streamable_columns,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
