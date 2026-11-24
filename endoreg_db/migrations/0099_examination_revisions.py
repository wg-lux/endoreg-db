from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0098_patient_medication_period")]

    operations = [
        migrations.CreateModel(
            name="ExaminationRevision",
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
                ("title", models.CharField(max_length=255)),
                ("version", models.PositiveIntegerField(default=1)),
                ("snapshot", models.JSONField()),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "patient_examination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revisions",
                        to="endoreg_db.patientexamination",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="CaseExaminationRecord",
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
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="selected_examinations",
                        to="endoreg_db.case",
                    ),
                ),
                (
                    "patient_examination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.patientexamination",
                    ),
                ),
                (
                    "revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="endoreg_db.examinationrevision",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("case", "patient_examination"),
                        name="one_selected_revision_per_case_exam",
                    )
                ]
            },
        ),
    ]
