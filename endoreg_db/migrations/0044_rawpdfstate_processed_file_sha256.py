from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0043_rename_datetime_patientlabvalue_timestamp_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="rawpdfstate",
            name="processed_file_sha256",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "SHA-256 of the plaintext anonymized PDF currently attached to "
                    "the RawPdfFile. Empty until a processed artifact has been "
                    "verified."
                ),
                max_length=64,
            ),
        ),
    ]
