import endoreg_db.utils.encryption.encrypted
from django.core.validators import FileExtensionValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0074_videohlsartifact_source_content_hash")]

    operations = [
        migrations.AlterField(
            model_name="modelmeta",
            name="weights",
            field=models.FileField(
                blank=True,
                help_text="Path to the model weights file (.safetensors), relative to MEDIA_ROOT.",
                null=True,
                storage=endoreg_db.utils.encryption.encrypted.LazyEncryptedStorage(),
                upload_to="model_weights",
                validators=[
                    FileExtensionValidator(
                        allowed_extensions=["safetensors", "pth", "pt"]
                    )
                ],
            ),
        ),
    ]
