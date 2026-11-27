from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """Reject invalid presence fractions without rewriting existing planning data."""

    dependencies = [("endoreg_db", "0116_studyreporttextextraction")]

    operations = [
        migrations.AlterField(
            model_name="centershift",
            name="estimated_presence_fraction",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("0"),
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("1")),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="centershift",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    estimated_presence_fraction__gte=Decimal("0"),
                    estimated_presence_fraction__lte=Decimal("1"),
                ),
                name="center_shift_presence_fraction_range",
            ),
        ),
    ]
