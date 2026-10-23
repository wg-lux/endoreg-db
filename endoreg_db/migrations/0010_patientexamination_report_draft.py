from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0009_patientexamination_knowledge_base_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="patientexamination",
            name="report_draft",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="patientexamination",
            name="draft_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
