from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("endoreg_db", "0012_networknode_transferjob"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Requirement",
        ),
        migrations.DeleteModel(
            name="RequirementSet",
        ),
        migrations.DeleteModel(
            name="ExaminationRequirementSet",
        ),
        migrations.DeleteModel(
            name="RequirementOperator",
        ),
        migrations.DeleteModel(
            name="RequirementSetType",
        ),
        migrations.DeleteModel(
            name="RequirementType",
        ),
    ]
