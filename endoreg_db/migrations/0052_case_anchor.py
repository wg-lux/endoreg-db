import uuid

from django.db import migrations, models


def populate_case_ids(apps, schema_editor):
    case_model = apps.get_model("endoreg_db", "Case")
    for case in case_model.objects.filter(case_id__isnull=True).iterator():
        case.case_id = uuid.uuid4()
        case.save(update_fields=["case_id"])


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0051_portaluserinfo_centers")]

    operations = [
        migrations.AddField(
            model_name="case",
            name="case_id",
            field=models.UUIDField(
                db_index=True,
                editable=False,
                help_text="Stable public identifier for the clinical case.",
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(populate_case_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="case",
            name="case_id",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
                help_text="Stable public identifier for the clinical case.",
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_lab_samples",
            field=models.ManyToManyField(
                blank=True,
                related_name="cases",
                to="endoreg_db.patientlabsample",
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_lab_values",
            field=models.ManyToManyField(
                blank=True,
                related_name="cases",
                to="endoreg_db.patientlabvalue",
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_medication_schedules",
            field=models.ManyToManyField(
                blank=True,
                related_name="cases",
                to="endoreg_db.patientmedicationschedule",
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_medications",
            field=models.ManyToManyField(
                blank=True,
                related_name="cases",
                to="endoreg_db.patientmedication",
            ),
        ),
        migrations.AddConstraint(
            model_name="case",
            constraint=models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="case_end_not_before_start",
            ),
        ),
    ]
