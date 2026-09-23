from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "endoreg_db",
            "0039_rename_report_llm_pdf_oper_1bf524_idx_report_llm__pdf_id_6995a6_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="patientexamination",
            name="dtypes_record",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="patientexamination",
            name="dtypes_record_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
