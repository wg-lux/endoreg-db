from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0078_video_joined_dataset"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(
            model_name="uploadjob",
            name="status",
            field=models.CharField(
                max_length=20,
                default="pending",
                help_text="Current processing status of the upload",
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("retrying", "Retrying"),
                    ("anonymized", "Anonymized"),
                    ("error", "Error"),
                    ("lost", "Lost"),
                    ("cancel_requested", "Cancellation requested"),
                    ("cancelled", "Cancelled"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cancellation_requested_at",
            field=models.DateTimeField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="cancellation_requested_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cancelled_upload_jobs",
            ),
        ),
    ]
