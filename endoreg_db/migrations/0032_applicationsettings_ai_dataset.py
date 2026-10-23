from django.db import migrations, models
import django.db.models.deletion


def backfill_ai_dataset(apps, schema_editor):
    application_settings_model = apps.get_model("endoreg_db", "ApplicationSettings")
    ai_dataset_model = apps.get_model("endoreg_db", "AIDataSet")

    for settings_obj in application_settings_model.objects.all():
        dataset_name = (settings_obj.ai_dataset_name or "").strip()
        dataset_type = (settings_obj.ai_dataset_type or "").strip()
        if not dataset_name or not dataset_type:
            continue

        matches = list(
            ai_dataset_model.objects.filter(
                name=dataset_name,
                dataset_type=dataset_type,
            ).order_by("pk")[:2]
        )
        if len(matches) != 1:
            continue

        settings_obj.ai_dataset_id = matches[0].pk
        settings_obj.save(update_fields=["ai_dataset"])


class Migration(migrations.Migration):
    dependencies = [
        (
            "endoreg_db",
            "0031_rename_frame_extra_video_i_f515a6_idx_frame_extra_video_i_e672aa_idx_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="applicationsettings",
            name="ai_dataset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="endoreg_db.aidataset",
            ),
        ),
        migrations.RunPython(backfill_ai_dataset, migrations.RunPython.noop),
    ]
