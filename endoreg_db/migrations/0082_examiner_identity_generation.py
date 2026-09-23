from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0081_sensitive_meta_identity_fingerprint")]

    operations = [
        migrations.AddField(
            model_name="examiner",
            name="identity_salt_fingerprint",
            field=models.CharField(
                max_length=64, blank=True, default="", editable=False
            ),
        ),
        migrations.AddField(
            model_name="examiner",
            name="identity_fingerprint",
            field=models.CharField(
                max_length=64, blank=True, default="", editable=False
            ),
        ),
    ]
