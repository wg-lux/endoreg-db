from io import StringIO
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.core.management import call_command
from django.test import override_settings
from django.db import connections
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.terminology.terminology_loader import get_terminology_service

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.services.reference_data.clinical_projection import (
    clinical_snapshot,
    import_clinical_references,
    plan_clinical_reference_import,
)
from endoreg_db.services.centers.employees import center_employee_overrides
from endoreg_db.services.studies.presets import import_study_preset
from endoreg_db.utils.file_operations import atomic_write_file

pytestmark = pytest.mark.django_db


def package(*, employees: bool = False, missing_finding: bool = False) -> KnowledgeBase:
    return KnowledgeBase.model_validate(
        {
            "config": {"name": "local_preset", "version": "1.0.0"},
            "study_preset": {
                "setup": {
                    "name": "setup",
                    "centers": [{"name": "clinic"}],
                    "genders": [{"name": "unknown", "abbreviation": "?"}],
                    "label_types": [{"name": "quality"}],
                    "labels": [{"name": "low_quality", "label_type": "quality"}],
                    "label_sets": [
                        {"name": "review", "version": 1, "labels": ["low_quality"]}
                    ],
                }
            },
            "examination": {
                "inspection": {
                    "name": "inspection",
                    "findings": ["missing"] if missing_finding else [],
                }
            },
            "center_employee_list": {
                "staff": {
                    "name": "staff",
                    "examiners": [
                        {
                            "center": "clinic",
                            "first_name": "Test",
                            "last_name": "Employee",
                        }
                    ],
                }
            }
            if employees
            else {},
        }
    )


@pytest.mark.parametrize("employees", [False, True])
def test_preset_creates_reference_rows_without_host_yaml_and_preserves_ids(
    employees: bool,
) -> None:
    kb = package(employees=employees)
    import_study_preset(kb)
    center = Center.objects.get(center_key="clinic")
    gender = Gender.objects.get(name="unknown")
    label = Label.objects.get(name="low_quality")
    examination = Examination.objects.get(name="inspection")
    import_study_preset(kb)
    assert Center.objects.get(center_key="clinic").pk == center.pk
    assert Gender.objects.get(name="unknown").pk == gender.pk
    assert Label.objects.get(name="low_quality").pk == label.pk
    assert Examination.objects.get(name="inspection").pk == examination.pk
    assert list(LabelSet.objects.get(name="review").labels.all()) == [label]
    assert bool(center_employee_overrides(center)) is employees


def test_missing_clinical_reference_rolls_back_entire_preset() -> None:
    with pytest.raises(ObjectDoesNotExist):
        import_study_preset(package(missing_finding=True))
    assert not Center.objects.exists()
    assert not Gender.objects.exists()
    assert not Label.objects.exists()


@pytest.mark.parametrize(
    "change", ["description", "findings", "indications", "examination_types"]
)
def test_preset_cannot_reinterpret_a_captured_clinical_examination(change: str) -> None:
    clinical = KnowledgeBase.model_validate(
        {
            "config": {"name": "clinical_origin", "version": "1.0.0"},
            "finding": {"lesion": {"name": "lesion"}},
            "indication": {"screening": {"name": "screening"}},
            "examination_type": {"endoscopy": {"name": "endoscopy"}},
            "examination": {
                "inspection": {
                    "name": "inspection",
                    "description": "Reviewed clinical definition",
                    "findings": ["lesion"],
                    "indications": ["screening"],
                    "examination_types": ["endoscopy"],
                }
            },
        }
    )
    original = clinical_snapshot(clinical)
    import_clinical_references(original)
    examination = Examination.objects.get(name="inspection")
    patient = Patient.objects.create(patient_hash="preset-history")
    captured = PatientExamination.objects.create(
        patient=patient, examination=examination
    )
    preset = package(employees=True)
    definition = clinical.examination["inspection"].model_dump()
    definition[change] = "Changed definition" if change == "description" else []
    preset.examination["inspection"] = type(
        clinical.examination["inspection"]
    ).model_validate(definition)
    with pytest.raises(ValueError, match=f"conflicts.*{change}"):
        import_study_preset(preset)
    captured.refresh_from_db()
    assert captured.examination_id == examination.pk
    assert plan_clinical_reference_import(original).unchanged
    assert not Center.objects.exists()
    assert not Gender.objects.exists()
    assert not Label.objects.exists()


@pytest.mark.parametrize("change", ["gender", "label", "label_set"])
def test_preset_rejects_mutation_of_existing_reference_definitions(change: str) -> None:
    original = package()
    import_study_preset(original)
    changed = package()
    preset = changed.study_preset["setup"]
    if change == "gender":
        preset.genders[0].description = "Changed meaning"
    elif change == "label":
        preset.labels[0].description = "Changed meaning"
    else:
        preset.label_sets[0].labels = []
    with pytest.raises(ValueError, match="Preset conflicts"):
        import_study_preset(changed)
    import_study_preset(original)


def test_preset_reuses_equivalent_examination_relations() -> None:
    definition = package()
    Finding.objects.create(name="lesion")
    definition.examination["inspection"].findings = ["lesion"]
    import_study_preset(definition)
    first = Examination.objects.get(name="inspection")
    import_study_preset(definition)
    assert Examination.objects.get(name="inspection").pk == first.pk
    assert list(first.findings.values_list("name", flat=True)) == ["lesion"]


def test_ambiguous_existing_labels_fail_without_overwriting() -> None:
    Label.objects.create(name="low_quality", description="first")
    Label.objects.create(name="low_quality", description="second")
    with pytest.raises(MultipleObjectsReturned):
        import_study_preset(package())
    assert set(Label.objects.values_list("description", flat=True)) == {
        "first",
        "second",
    }
    assert not Center.objects.exists()


def test_command_reads_shared_terminology_service() -> None:
    service = Mock(load=Mock(return_value=package()))
    with patch(
        "endoreg_db.management.commands.import_study_preset.get_terminology_service",
        return_value=service,
    ):
        call_command(
            "import_study_preset",
            module="local_preset",
            module_version="1.0.0",
            stdout=StringIO(),
        )
    service.load.assert_called_once_with("local_preset", "1.0.0")
    assert Center.objects.filter(center_key="clinic").exists()


def test_command_loads_real_registered_yaml_package(tmp_path: Path) -> None:
    module = tmp_path / "packages" / "local_preset"
    atomic_write_file(
        destination=module / "config.yaml",
        content=[b"name: local_preset\nversion: 1.0.0\ndata:\n  files: [preset.yml]\n"],
    )
    atomic_write_file(
        destination=module / "preset.yml",
        content=[
            b"- model: study_preset\n  name: setup\n  centers:\n    - name: clinic\n  genders:\n    - name: unknown\n- model: examination\n  name: inspection\n"
        ],
    )
    registry = {
        "modules": {
            "local_preset": {
                "1.0.0": {
                    "sources": [
                        {"kind": "filesystem", "input_dirs": [str(module.parent)]}
                    ]
                }
            }
        }
    }
    atomic_write_file(
        destination=tmp_path / "registry.json", content=[json.dumps(registry).encode()]
    )
    get_terminology_service.cache_clear()
    try:
        with override_settings(TERMINOLOGY_ROOT=tmp_path):
            call_command(
                "import_study_preset",
                module="local_preset",
                module_version="1.0.0",
                stdout=StringIO(),
            )
    finally:
        get_terminology_service.cache_clear()
    assert Examination.objects.filter(name="inspection").exists()
    assert Gender.objects.filter(name="unknown").exists()
    assert center_employee_overrides(Center.objects.get(center_key="clinic")) == {}


@pytest.mark.django_db(transaction=True)
def test_concurrent_preset_imports_preserve_one_identity() -> None:
    def import_once() -> None:
        try:
            import_study_preset(package(employees=True))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(import_once) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    assert Center.objects.filter(center_key="clinic").count() == 1
    assert Gender.objects.filter(name="unknown").count() == 1
    assert Label.objects.filter(name="low_quality").count() == 1
    assert Examination.objects.filter(name="inspection").count() == 1
