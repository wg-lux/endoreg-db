from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0080_media_transcode_lease")]

    operations = [
        migrations.AddField(
            model_name="sensitivemeta",
            name="identity_fingerprint",
            field=models.CharField(
                max_length=64, blank=True, default="", editable=False
            ),
        ),
        migrations.AddField(
            model_name="sensitivemeta",
            name="identity_salt_fingerprint",
            field=models.CharField(
                max_length=64, blank=True, default="", editable=False
            ),
        ),
    ]
