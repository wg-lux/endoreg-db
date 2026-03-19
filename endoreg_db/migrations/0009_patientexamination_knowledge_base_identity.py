from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0008_imageclassificationannotation_upsert_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="patientexamination",
            name="knowledge_base_module",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="patientexamination",
            name="knowledge_base_version",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
