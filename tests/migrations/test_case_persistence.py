from __future__ import annotations

from uuid import UUID

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_case_anchor_migration_backfills_stable_case_id() -> None:
    migrate_from = [("endoreg_db", "0051_portaluserinfo_centers")]
    migrate_to = [("endoreg_db", "0052_case_anchor")]
    executor = MigrationExecutor(connection)

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        patient_model = old_apps.get_model("endoreg_db", "Patient")
        case_model = old_apps.get_model("endoreg_db", "Case")
        patient = patient_model.objects.create(
            patient_hash="case-migration-patient",
            first_name="Case",
            last_name="Migration",
        )
        patient_case = case_model.objects.create(
            patient_id=patient.pk,
            start_date=timezone.now(),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        migrated_apps = executor.loader.project_state(migrate_to).apps
        migrated_case_model = migrated_apps.get_model("endoreg_db", "Case")
        migrated_case = migrated_case_model.objects.get(pk=patient_case.pk)

        assert isinstance(migrated_case.case_id, UUID)
        assert migrated_case.patient_medications.count() == 0
        assert migrated_case.patient_medication_schedules.count() == 0
        assert migrated_case.patient_lab_samples.count() == 0
        assert migrated_case.patient_lab_values.count() == 0
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
