"""Backfill current diagnostics and prohibit stale errors on non-failed jobs."""

from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def clear_stale_diagnostics(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    jobs = apps.get_model("endoreg_db", "UploadJob").objects.using(
        schema_editor.connection.alias
    )
    jobs.exclude(
        status__in=["retrying", "error", "lost", "cancel_requested", "cancelled"]
    ).exclude(error_code="", error_detail="").update(error_code="", error_detail="")


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0085_rawpdf_artifact_path_lengths")]
    operations = [
        migrations.RunPython(clear_stale_diagnostics, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="uploadjob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=[
                            "retrying",
                            "error",
                            "lost",
                            "cancel_requested",
                            "cancelled",
                        ]
                    )
                    | models.Q(error_code="", error_detail="")
                ),
                name="upload_job_current_error_state",
            ),
        ),
    ]
