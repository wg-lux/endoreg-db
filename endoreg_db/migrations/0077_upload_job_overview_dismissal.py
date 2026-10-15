from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0076_operation_ownership_ledgers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadjob",
            name="overview_dismissed_at",
            field=models.DateTimeField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="uploadjob",
            name="overview_dismissed_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                null=True,
                blank=True,
                editable=False,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dismissed_upload_jobs",
            ),
        ),
    ]
