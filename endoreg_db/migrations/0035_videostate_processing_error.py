from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0034_anonymization_metrics"),
    ]

    operations = [
        migrations.AddField(
            model_name="videostate",
            name="processing_error",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True if processing failed or media integrity marked this video lost."
                ),
            ),
        ),
    ]
