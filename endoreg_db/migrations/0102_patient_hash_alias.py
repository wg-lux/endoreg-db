from django.core.validators import RegexValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0101_upload_job_import_interrupted")]

    operations = [
        migrations.CreateModel(
            name="PatientHashAlias",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "patient_hash",
                    models.CharField(
                        max_length=64, validators=[RegexValidator(r"\A[0-9a-f]{64}\Z")]
                    ),
                ),
                (
                    "salt_fingerprint",
                    models.CharField(
                        max_length=64, validators=[RegexValidator(r"\A[0-9a-f]{64}\Z")]
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "center",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.center",
                    ),
                ),
                (
                    "patient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.patient",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("center", "patient_hash"),
                        name="unique_patient_hash_alias_center",
                    )
                ]
            },
        ),
    ]
