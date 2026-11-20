from datetime import time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.center.center_shift import CenterShift
from endoreg_db.models.administration.shift.shift import Shift


@pytest.fixture
def center_shift() -> CenterShift:
    return CenterShift.objects.create(
        name="presence-validation",
        center=Center.objects.create(name="presence-validation"),
        shift=Shift.objects.create(name="day"),
        start_time=time(8),
        end_time=time(16),
        estimated_presence_fraction=Decimal("0.5"),
    )


@pytest.mark.parametrize("fraction", [Decimal("0"), Decimal("0.5"), Decimal("1")])
def test_presence_fraction_accepts_inclusive_unit_interval(
    center_shift: CenterShift, fraction: Decimal
) -> None:
    center_shift.estimated_presence_fraction = fraction
    center_shift.full_clean()
    center_shift.save(update_fields=["estimated_presence_fraction"])

    center_shift.refresh_from_db()
    assert center_shift.estimated_presence_fraction == fraction


@pytest.mark.parametrize("fraction", [Decimal("-0.0001"), Decimal("1.0001")])
def test_presence_fraction_rejects_invalid_model_values(
    center_shift: CenterShift, fraction: Decimal
) -> None:
    center_shift.estimated_presence_fraction = fraction

    with pytest.raises(ValidationError) as error:
        center_shift.full_clean()

    assert "estimated_presence_fraction" in error.value.message_dict


@pytest.mark.parametrize("fraction", [Decimal("-0.0001"), Decimal("1.0001")])
def test_presence_fraction_database_constraint_blocks_query_updates(
    center_shift: CenterShift, fraction: Decimal
) -> None:
    with pytest.raises(IntegrityError, match="center_shift_presence_fraction_range"):
        with transaction.atomic():
            CenterShift.objects.filter(pk=center_shift.pk).update(
                estimated_presence_fraction=fraction
            )

    center_shift.refresh_from_db()
    assert center_shift.estimated_presence_fraction == Decimal("0.5")


@pytest.mark.no_db
def test_scheduled_day_initialization_is_explicitly_unimplemented() -> None:
    with pytest.raises(NotImplementedError, match="Scheduled-day initialization"):
        CenterShift().initialize_scheduled_days()


@pytest.mark.django_db(transaction=True)
def test_presence_constraint_migration_preserves_invalid_existing_data(
    center_shift: CenterShift,
) -> None:
    before = [("endoreg_db", "0116_studyreporttextextraction")]
    after = [("endoreg_db", "0117_center_shift_presence_fraction")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(before)
        CenterShift.objects.filter(pk=center_shift.pk).update(
            estimated_presence_fraction=Decimal("1.0001")
        )

        with pytest.raises(
            IntegrityError, match="center_shift_presence_fraction_range"
        ):
            MigrationExecutor(connection).migrate(after)

        center_shift.refresh_from_db()
        assert center_shift.estimated_presence_fraction == Decimal("1.0001")
        assert MigrationExecutor(connection).migration_plan(after)
    finally:
        CenterShift.objects.filter(pk=center_shift.pk).update(
            estimated_presence_fraction=Decimal("0.5")
        )
        MigrationExecutor(connection).migrate(latest)

    center_shift.refresh_from_db()
    assert center_shift.estimated_presence_fraction == Decimal("0.5")
    assert not MigrationExecutor(connection).migration_plan(latest)
