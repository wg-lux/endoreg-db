"""Allow canonical content-hash and generation paths without truncation."""

from django.core.validators import FileExtensionValidator
from django.db import migrations, models

from endoreg_db.utils.encryption.encrypted import LazyEncryptedStorage


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0084_patient_finding_instances")]

    operations = [
        migrations.AlterField(
            model_name="rawpdffile",
            name="file",
            field=models.FileField(
                max_length=500,
                upload_to="sensitive_reports",
                storage=LazyEncryptedStorage(),
                validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
            ),
        ),
        migrations.AlterField(
            model_name="rawpdffile",
            name="processed_file",
            field=models.FileField(
                max_length=500,
                upload_to="processed_reports_final",
                storage=LazyEncryptedStorage(),
                validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
                blank=True,
                null=True,
            ),
        ),
    ]
