from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0124_reconcile_study_import_receipts")]

    operations = [
        migrations.AddField(
            model_name="portaluserinfo",
            name="annotation_profession",
            field=models.CharField(
                max_length=32,
                null=True,
                blank=True,
                choices=[
                    ("physician", "Ärztin/Arzt"),
                    ("medical_student", "Medizinstudierende"),
                    ("nurse", "Pflegefachkraft"),
                    ("medical_assistant", "Medizinische Fachassistenz"),
                    ("medical_informatics", "Medizininformatik"),
                    ("non_medical", "Nichtmedizinisches Personal"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="portaluserinfo",
            name="annotation_academic_titles",
            field=models.JSONField(default=list, blank=True),
        ),
        migrations.AddConstraint(
            model_name="portaluserinfo",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    annotation_profession__in=[
                        "physician",
                        "medical_student",
                        "nurse",
                        "medical_assistant",
                        "medical_informatics",
                        "non_medical",
                    ]
                )
                | models.Q(annotation_profession__isnull=True),
                name="annotator_profile_valid_profession",
            ),
        ),
    ]
