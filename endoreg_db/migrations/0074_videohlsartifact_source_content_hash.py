from django.db import migrations, models


# Existing READY rows intentionally remain blank. The application rejects those
# rows at playlist, key, and segment lookup and queues bounded rematerialization;
# a schema migration must not decrypt and hash production videos.


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0073_upload_job_cleanup_receipt")]

    operations = [
        migrations.AddField(
            model_name="videohlsartifact",
            name="source_content_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA-256 digest of the exact plaintext source generation.",
                max_length=64,
            ),
        ),
    ]
