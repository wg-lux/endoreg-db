"""Upgrade a pre-catalogue database using independently authored legacy seeds."""

from importlib.resources import files
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.interface.DataLoader import DataLoader
from pydantic import TypeAdapter

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_type import ExaminationType
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.reference_catalog import (
    catalog_snapshot,
    import_reference_catalog,
    plan_reference_catalog,
)


def _legacy_fields(resource: str, name: str) -> JsonObject:
    rows = TypeAdapter(list[JsonObject]).validate_python(
        yaml.safe_load(files("endoreg_db.data").joinpath(resource).read_text())
    )
    for row in rows:
        fields = row["fields"]
        if isinstance(fields, dict) and fields.get("name") == name:
            return fields
    raise AssertionError(f"Missing legacy seed {resource}: {name}")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["success", "conflict", "receipt_failure"])
def test_pre_catalogue_upgrade_preserves_clinical_history(outcome: str) -> None:
    before = [("endoreg_db", "0086_upload_job_current_error_state")]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(before)
        old = executor.loader.project_state(before).apps
        # These definitions come from the retired host fixtures, never from the
        # new importer, its exporter, or its versioned catalogue snapshot.
        examination_fields = _legacy_fields(
            "examination/examinations/data.yaml", "capsule_endoscopy"
        )
        indication_fields = _legacy_fields(
            "examination_indication/endoscopy.yaml", "capsule_endoscopy_generic"
        )
        type_fields = _legacy_fields("examination/type/data.yaml", "endoscopy")
        old_type = cast(
            type[ExaminationType], old.get_model("endoreg_db", "ExaminationType")
        )
        old_indication = cast(
            type[ExaminationIndication],
            old.get_model("endoreg_db", "ExaminationIndication"),
        )
        old_examination = cast(
            type[Examination], old.get_model("endoreg_db", "Examination")
        )
        old_patient = cast(type[Patient], old.get_model("endoreg_db", "Patient"))
        old_capture = cast(
            type[PatientExamination], old.get_model("endoreg_db", "PatientExamination")
        )
        old_report = cast(
            type[PatientExaminationReport],
            old.get_model("endoreg_db", "PatientExaminationReport"),
        )
        examination_type = old_type.objects.create(name=type_fields["name"])
        indication = old_indication.objects.create(
            name=indication_fields["name"], description=indication_fields["description"]
        )
        examination = old_examination.objects.create(
            name=examination_fields["name"],
            description=examination_fields.get("description"),
        )
        assert examination_fields["examination_types"] == [examination_type.name]
        assert examination_fields["indications"] == [indication.name]
        examination.examination_types.add(examination_type)
        examination.indications.add(indication)
        patient = old_patient.objects.create(patient_hash="legacy-catalogue-upgrade")
        capture = old_capture.objects.create(
            patient=patient, examination=examination, hash="legacy-capture"
        )
        report = old_report.objects.create(
            patient_examination=capture,
            template_name="legacy",
            rendered_text="Historischer Befund",
        )
        if outcome == "conflict":
            examination.description = "Locally reviewed definition"
            examination.save(update_fields=["description"])

        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        assert not ReferenceCatalogImport.objects.exists()
        kb = DataLoader(
            input_dirs=[Path(str(files("lx_dtypes").joinpath("data")))]
        ).load_knowledge_base("endoreg_reference")
        source = catalog_snapshot(kb)
        plan = plan_reference_catalog(source, adopt_existing=True)
        if outcome == "success":
            assert plan.can_import
            import_reference_catalog(source, adopt_existing=True)
            assert import_reference_catalog(source).unchanged
        elif outcome == "conflict":
            assert not plan.can_import
            with pytest.raises(ValueError, match="conflicts"):
                import_reference_catalog(source, adopt_existing=True)
        else:
            with patch.object(
                ReferenceCatalogImport,
                "save",
                side_effect=ValueError("receipt failure"),
            ):
                with pytest.raises(ValueError, match="receipt failure"):
                    import_reference_catalog(source, adopt_existing=True)
        if outcome != "success":
            assert not ReferenceCatalogImport.objects.exists()
            assert Examination.objects.count() == 1
            assert ExaminationType.objects.count() == 1
            assert ExaminationIndication.objects.count() == 1
        persisted = Examination.objects.get(pk=examination.pk)
        assert persisted.description == examination.description
        assert list(persisted.examination_types.values_list("pk", flat=True)) == [
            examination_type.pk
        ]
        assert list(persisted.indications.values_list("pk", flat=True)) == [
            indication.pk
        ]
        captured = PatientExamination.objects.get(pk=capture.pk)
        assert captured.patient_id == patient.pk
        assert captured.examination_id == examination.pk
        historical = PatientExaminationReport.objects.get(pk=report.pk)
        assert historical.patient_examination == captured
        assert historical.rendered_text == "Historischer Befund"
        assert historical.dtypes_record is None
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
