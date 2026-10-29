from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("endoreg_db", "0088_clinical_reference_import")]

    operations = [
        migrations.AlterField(
            model_name="unit",
            name="description",
            field=models.TextField(blank=True, null=True),
        )
    ]
