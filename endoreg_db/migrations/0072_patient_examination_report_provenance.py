from django.db import migrations, models


def legacy_report_language(editor_payload, *, report_id):
    if not isinstance(editor_payload, dict):
        raise ValueError(
            f"Report {report_id} editor_payload must be a JSON object for provenance backfill"
        )
    snake_language = editor_payload.get("report_language")
    camel_language = editor_payload.get("reportLanguage")
    if (
        snake_language is not None
        and camel_language is not None
        and snake_language != camel_language
    ):
        raise ValueError(f"Report {report_id} has conflicting report language aliases")
    language = (
        snake_language
        if snake_language is not None
        else camel_language
        if camel_language is not None
        else "de"
    )
    if language not in {"de", "en"}:
        raise ValueError(
            f"Report {report_id} has unsupported report language {language!r}"
        )
    canonical_payload = dict(editor_payload)
    canonical_payload.pop("reportLanguage", None)
    canonical_payload["report_language"] = language
    return language, canonical_payload


def backfill_report_knowledge_base_identity(apps, schema_editor):
    del schema_editor
    report_model = apps.get_model("endoreg_db", "PatientExaminationReport")
    for report in report_model.objects.select_related("patient_examination").iterator():
        patient_examination = report.patient_examination
        language, editor_payload = legacy_report_language(
            report.editor_payload,
            report_id=report.pk,
        )
        report.knowledge_base_module = patient_examination.knowledge_base_module
        report.knowledge_base_version = patient_examination.knowledge_base_version
        report.language = language
        report.editor_payload = editor_payload
        report.save(
            update_fields=[
                "knowledge_base_module",
                "knowledge_base_version",
                "language",
                "editor_payload",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0071_storage_operator_control")]

    operations = [
        migrations.AddField(
            model_name="patientexaminationreport",
            name="knowledge_base_module",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="patientexaminationreport",
            name="knowledge_base_version",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="patientexaminationreport",
            name="language",
            field=models.CharField(
                choices=[("de", "Deutsch"), ("en", "English")],
                default="de",
                max_length=2,
            ),
        ),
        migrations.AddField(
            model_name="patientexaminationreport",
            name="runtime_validation_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            backfill_report_knowledge_base_identity,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
