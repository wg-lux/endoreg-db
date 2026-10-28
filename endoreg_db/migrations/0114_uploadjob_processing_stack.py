from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0112_alter_center_center_key")]
    operations = [
        migrations.AddField(
            model_name="uploadjob",
            name="processing_stack",
            field=models.JSONField(
                null=True,
                blank=True,
                default=None,
                help_text="Rust-owned remaining import stages and stable checkpoint reference.",
            ),
        )
    ]
