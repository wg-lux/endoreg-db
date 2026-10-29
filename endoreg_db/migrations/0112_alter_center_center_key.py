from django.db import migrations, models


class Migration(migrations.Migration):
    """Require a center key in model validation while retaining database uniqueness."""

    dependencies = [("endoreg_db", "0111_delete_frameextractionrequest")]

    operations = [
        migrations.AlterField(
            model_name="center",
            name="center_key",
            field=models.CharField(max_length=255, unique=True),
        ),
    ]
