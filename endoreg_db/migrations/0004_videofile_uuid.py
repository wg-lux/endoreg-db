import uuid

from django.db import migrations, models


def add_videofile_uuid_if_missing(apps, schema_editor):
    video_file_model = apps.get_model("endoreg_db", "VideoFile")
    table_name = video_file_model._meta.db_table
    column_name = "uuid"

    with schema_editor.connection.cursor() as cursor:
        columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    if column_name in columns:
        return

    field = models.UUIDField(null=True, editable=False)
    field.set_attributes_from_name(column_name)
    column_type = field.db_type(schema_editor.connection)
    if not column_type:
        raise RuntimeError(
            f"Could not resolve database type for {table_name}.{column_name}"
        )

    quoted_table_name = schema_editor.quote_name(table_name)
    quoted_column_name = schema_editor.quote_name(column_name)
    schema_editor.execute(
        f"ALTER TABLE {quoted_table_name} ADD COLUMN {quoted_column_name} {column_type} NULL"
    )


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
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    add_videofile_uuid_if_missing,
                    reverse_code=migrations.RunPython.noop,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="videofile",
                    name="uuid",
                    field=models.UUIDField(null=True, editable=False),
                ),
            ],
        ),
        migrations.RunPython(populate_videofile_uuid, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="videofile",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
