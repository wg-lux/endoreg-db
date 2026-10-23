from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0103_patient_examination_uuid")]
    operations = [
        migrations.CreateModel(
            name="PatientMatchingBudget",
            fields=[
                ("source_uuid", models.UUIDField(primary_key=True, serialize=False)),
                ("requests_used", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="PatientMatchingSession",
            fields=[
                ("session_id", models.UUIDField(primary_key=True, serialize=False)),
                ("request_json", models.TextField()),
                ("completion_json", models.TextField(default="")),
                ("signatures_json", models.TextField(default="")),
                ("authorized_by", models.CharField(max_length=255)),
                ("completed_by", models.CharField(default="", max_length=255)),
                ("consumed", models.BooleanField(default=False)),
            ],
            options={
                "permissions": [
                    (
                        "authorize_patient_matching",
                        "Authorize bounded patient matching",
                    ),
                    (
                        "execute_patient_matching",
                        "Record local patient matching computation",
                    ),
                    (
                        "accept_patient_matching",
                        "Accept authenticated patient matching result",
                    ),
                ]
            },
        ),
        migrations.CreateModel(
            name="PatientMatchingRelationship",
            fields=[
                (
                    "session",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        serialize=False,
                        to="endoreg_db.patientmatchingsession",
                    ),
                ),
                ("source_uuid", models.UUIDField()),
                ("candidate_uuid", models.UUIDField()),
            ],
        ),
    ]
