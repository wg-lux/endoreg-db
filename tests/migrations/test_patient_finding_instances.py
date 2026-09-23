from __future__ import annotations

from uuid import UUID

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_finding_instance_migration_preserves_relations_and_guards_downgrade() -> None:
    before = [("endoreg_db", "0083_repair_raw_video_hash_column")]
    after = [("endoreg_db", "0084_patient_finding_instances")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        patient = old.get_model("endoreg_db", "Patient").objects.create(
            patient_hash="finding-migration"
        )
        examination = old.get_model("endoreg_db", "PatientExamination").objects.create(
            patient_id=patient.pk
        )
        finding_type = old.get_model("endoreg_db", "Finding").objects.create(
            name="migration_polyp"
        )
        finding = old.get_model("endoreg_db", "PatientFinding").objects.create(
            patient_examination_id=examination.pk, finding_id=finding_type.pk
        )
        intervention_type = old.get_model(
            "endoreg_db", "FindingIntervention"
        ).objects.create(name="migration_resection")
        intervention = old.get_model(
            "endoreg_db", "PatientFindingIntervention"
        ).objects.create(finding_id=finding.pk, intervention_id=intervention_type.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        new = executor.loader.project_state(after).apps
        findings = new.get_model("endoreg_db", "PatientFinding").objects
        migrated = findings.get(pk=finding.pk)
        assert isinstance(migrated.instance_id, UUID)
        assert (
            new.get_model("endoreg_db", "PatientFindingIntervention")
            .objects.get(pk=intervention.pk)
            .finding_id
            == finding.pk
        )
        second = findings.create(
            patient_examination_id=examination.pk, finding_id=finding_type.pk
        )
        assert second.instance_id != migrated.instance_id
        with pytest.raises(RuntimeError, match="Cannot downgrade"):
            MigrationExecutor(connection).migrate(before)
        assert (
            findings.filter(pk__in=[finding.pk, second.pk], is_active=True).count() == 2
        )
        second.delete()
        MigrationExecutor(connection).migrate(before)
        assert (
            old.get_model("endoreg_db", "PatientFinding").objects.get(pk=finding.pk).pk
            == finding.pk
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
