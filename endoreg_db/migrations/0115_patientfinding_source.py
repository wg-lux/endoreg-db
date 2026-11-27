import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0113_frameannotationexportjob")]
    operations = [
        migrations.AddField(
            model_name="patientfinding",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual entry"),
                    ("report_text", "Approved report text suggestion"),
                    ("image_ai", "Approved image AI suggestion"),
                ],
                default="manual",
                help_text="Where the finding came from; set by the server, never by clients.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="patientfinding",
            name="source_report",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="derived_patient_findings",
                to="endoreg_db.rawpdffile",
            ),
        ),
    ]
