from __future__ import annotations

from uuid import UUID

import pytest
from django.core.exceptions import FieldDoesNotExist
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


@pytest.mark.django_db(transaction=True)
def test_multiple_document_migration_preserves_legacy_video_link() -> None:
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
            video_hash="multiple-document-migration-video",
        )
        patient_examination.video_id = video.pk
        patient_examination.save(update_fields=["video"])

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
