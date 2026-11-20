from uuid import UUID

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_uuid_backfill_preserves_existing_patient_examination_links() -> None:
    before = [("endoreg_db", "0102_patient_hash_alias")]
    after = [("endoreg_db", "0103_patient_examination_uuid")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        patients = old_apps.get_model("endoreg_db", "Patient")
        examinations = old_apps.get_model("endoreg_db", "PatientExamination")
        first = patients.objects.create(
            first_name="", last_name="", is_real_person=False
        )
        second = patients.objects.create(
            first_name="", last_name="", is_real_person=False
        )
        exam = examinations.objects.create(patient_id=first.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        migrated = apps.get_model("endoreg_db", "Patient")
        migrated_exam = apps.get_model("endoreg_db", "PatientExamination").objects.get(
            pk=exam.pk
        )
        values = [migrated.objects.get(pk=pk).uuid for pk in (first.pk, second.pk)]
        values.append(migrated_exam.uuid)
        assert all(isinstance(value, UUID) and value.version == 4 for value in values)
        assert len(set(values)) == 3
        assert migrated_exam.patient_id == first.pk

        # Reapplying the migration graph does not regenerate persistent identities.
        MigrationExecutor(connection).migrate(after)
        assert migrated.objects.get(pk=first.pk).uuid == values[0]
    finally:
        MigrationExecutor(connection).migrate(latest)
    assert not MigrationExecutor(connection).migration_plan(latest)
