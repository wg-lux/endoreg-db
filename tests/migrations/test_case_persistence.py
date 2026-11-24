from __future__ import annotations

from uuid import UUID
from typing import Literal

import pytest
from django.apps.registry import Apps
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_failed_upgrade_can_leave_historical_schema_for_isolated_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = [("endoreg_db", "0056_alter_videohlsartifact_error_code")]
    after = [("endoreg_db", "0057_patientexamination_multiple_documents")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)

    def fail_upgrade(_apps: Apps, _editor: BaseDatabaseSchemaEditor) -> None:
        raise RuntimeError("injected migration failure")

    executor = MigrationExecutor(connection)
    migration = executor.loader.get_migration(*after[0])
    monkeypatch.setattr(migration, "operations", [migrations.RunPython(fail_upgrade)])
    with pytest.raises(RuntimeError, match="injected migration failure"):
        executor.migrate(after)
    assert before[0] in executor.recorder.applied_migrations()
    assert after[0] not in executor.recorder.applied_migrations()
    # Deliberately do not restore the leaf. The fixture must discard this schema
    # and verify that the shared test schema's tables are unchanged.


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


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("existing_link", ["missing", "matching", "conflicting"])
def test_multiple_document_migration_preserves_legacy_video_link(
    existing_link: Literal["missing", "matching", "conflicting"],
) -> None:
    migrate_from = [("endoreg_db", "0056_alter_videohlsartifact_error_code")]
    migrate_to = [("endoreg_db", "0057_patientexamination_multiple_documents")]
    executor = MigrationExecutor(connection)

    try:
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        patient_model = old_apps.get_model("endoreg_db", "Patient")
        center_model = old_apps.get_model("endoreg_db", "Center")
        patient_examination_model = old_apps.get_model(
            "endoreg_db", "PatientExamination"
        )
        video_model = old_apps.get_model("endoreg_db", "VideoFile")

        center = center_model.objects.create(name="multiple-document-migration")
        patient = patient_model.objects.create(
            patient_hash="multiple-document-migration-patient",
            first_name="Multiple",
            last_name="Documents",
            center_id=center.pk,
        )
        patient_examination = patient_examination_model.objects.create(
            patient_id=patient.pk,
            hash="multiple-document-migration-examination",
        )
        video = video_model.objects.create(
            center_id=center.pk,
            patient_id=patient.pk,
            raw_video_hash="multiple-document-migration-video",
        )
        patient_examination.video_id = video.pk
        patient_examination.save(update_fields=["video"])

        if existing_link != "missing":
            linked_examination = (
                patient_examination_model.objects.create(
                    patient_id=patient.pk, hash="conflicting-examination"
                )
                if existing_link == "conflicting"
                else patient_examination
            )
            video.examination_id = linked_examination.pk
            video.save(update_fields=["examination"])

        if existing_link == "conflicting":
            with pytest.raises(RuntimeError, match="Conflicting PatientExamination"):
                MigrationExecutor(connection).migrate(migrate_to)
            patient_examination.refresh_from_db()
            video.refresh_from_db()
            assert patient_examination.video_id == video.pk
            assert video.examination_id != patient_examination.pk
            # Explicitly resolve the conflict so the same migration can be retried.
            video.examination_id = None
            video.save(update_fields=["examination"])

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        migrated_apps = executor.loader.project_state(migrate_to).apps
        migrated_video_model = migrated_apps.get_model("endoreg_db", "VideoFile")
        migrated_patient_examination_model = migrated_apps.get_model(
            "endoreg_db", "PatientExamination"
        )

        assert (
            migrated_video_model.objects.get(pk=video.pk).examination_id
            == patient_examination.pk
        )
        with pytest.raises(FieldDoesNotExist):
            migrated_patient_examination_model._meta.get_field("video")
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
