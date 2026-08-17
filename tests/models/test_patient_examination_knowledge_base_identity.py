from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Protocol, cast

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from pytest import MonkeyPatch
from pytest_django.fixtures import SettingsWrapper

from endoreg_db.models import Examination, Patient, PatientExamination
from endoreg_db.services.knowledge_base_identity import (
    get_configured_knowledge_base_identity,
)
from endoreg_db.serializers.patient_examination.patient_examination import (
    PatientExaminationSerializer,
)


class _SerializerErrors(Protocol):
    errors: Mapping[str, object]


def _serializer_errors(
    serializer: PatientExaminationSerializer,
) -> Mapping[str, object]:
    return cast(_SerializerErrors, serializer).errors


@pytest.mark.django_db
def test_patient_examination_save_stamps_knowledge_base_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="kb-patient-save",
        first_name="KB",
        last_name="Save",
    )

    def fake_configured_identity() -> tuple[str, str]:
        return "report_template_examples", "0.1.0"

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        fake_configured_identity,
    )

    patient_examination = PatientExamination.objects.create(patient=patient)

    assert patient_examination.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_version == "0.1.0"


@pytest.mark.django_db
def test_partial_update_persists_newly_assigned_knowledge_base_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="kb-patient-partial-update",
        first_name="KB",
        last_name="Partial Update",
    )

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        lambda: None,
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        lambda: ("active_module", "2026.08"),
    )
    patient_examination.date_start = date(2026, 8, 17)
    patient_examination.save(update_fields=["date_start"])
    patient_examination.refresh_from_db()

    assert patient_examination.knowledge_base_module == "active_module"
    assert patient_examination.knowledge_base_version == "2026.08"


@pytest.mark.django_db
def test_patient_examination_save_preserves_existing_knowledge_base_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="kb-patient-preserve",
        first_name="KB",
        last_name="Preserve",
    )

    def fake_configured_identity() -> tuple[str, str]:
        return "report_template_examples", "0.1.0"

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        fake_configured_identity,
    )

    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module="sealed_module",
        knowledge_base_version="2026.03",
    )

    assert patient_examination.knowledge_base_module == "sealed_module"
    assert patient_examination.knowledge_base_version == "2026.03"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("module_name", "version"),
    [("partial_module", ""), ("", "2026.03")],
)
def test_patient_examination_save_rejects_partial_knowledge_base_identity(
    monkeypatch: MonkeyPatch,
    module_name: str,
    version: str,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"kb-patient-partial-{module_name}-{version}",
        first_name="KB",
        last_name="Partial",
    )

    def unexpected_configured_identity() -> tuple[str, str]:
        raise AssertionError(
            "A partial identity must not be completed from configuration"
        )

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        unexpected_configured_identity,
    )

    with pytest.raises(DjangoValidationError, match="must be set together"):
        PatientExamination.objects.create(
            patient=patient,
            knowledge_base_module=module_name,
            knowledge_base_version=version,
        )


@pytest.mark.django_db
def test_patient_examination_serializer_create_stamps_knowledge_base_identity(
    monkeypatch: MonkeyPatch,
) -> None:
    Patient.objects.create(
        patient_hash="serializer-patient",
        first_name="Serializer",
        last_name="Patient",
    )
    Examination.objects.create(name="serializer_exam")

    def fake_configured_identity() -> tuple[str, str]:
        return "report_template_examples", "0.1.0"

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        fake_configured_identity,
    )

    serializer = PatientExaminationSerializer(
        data={
            "patient": "serializer-patient",
            "examination": "serializer_exam",
        }
    )

    assert serializer.is_valid(), _serializer_errors(serializer)
    patient_examination = serializer.save()

    assert patient_examination.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_version == "0.1.0"


def test_configured_knowledge_base_identity_uses_explicit_resolver_input_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: SettingsWrapper,
) -> None:
    configured_root = tmp_path / "configured-data"
    configured_root.mkdir(parents=True)
    settings.LX_DTYPES_KB_REGISTRY = ""
    settings.LOOKUP_DTYPES_DATA_ROOT = str(configured_root)

    def fake_resolve_dtypes_data_root() -> Path:
        return configured_root

    def fake_get_knowledge_base_identity(
        module_name: str,
        *,
        version: str | None = None,
        input_dirs: Sequence[Path] | None = None,
    ) -> tuple[str, str] | None:
        if (
            input_dirs == [configured_root]
            and version is None
            and module_name == "report_template_examples"
        ):
            return module_name, "resolved-1.2.3"
        return None

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity._resolve_dtypes_data_root",
        fake_resolve_dtypes_data_root,
    )
    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_knowledge_base_identity",
        fake_get_knowledge_base_identity,
    )

    assert get_configured_knowledge_base_identity() == (
        "report_template_examples",
        "resolved-1.2.3",
    )


def test_configured_knowledge_base_identity_uses_governed_active_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: SettingsWrapper,
) -> None:
    settings.LX_DTYPES_KB_REGISTRY = str(tmp_path / "registry.json")
    settings.LOOKUP_DTYPES_MODULE_NAME = "legacy_module"
    settings.LOOKUP_DTYPES_MODULE_VERSION = "legacy_version"
    settings.LOOKUP_DTYPES_DATA_ROOT = str(tmp_path / "legacy-data")

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.active_terminology_selection",
        lambda: ("active_module", "2026.08"),
    )

    def fake_get_knowledge_base_identity(
        module_name: str,
        *,
        version: str | None = None,
        input_dirs: Sequence[Path] | None = None,
    ) -> tuple[str, str]:
        assert module_name == "active_module"
        assert version == "2026.08"
        assert input_dirs is None
        return module_name, version

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_knowledge_base_identity",
        fake_get_knowledge_base_identity,
    )

    assert get_configured_knowledge_base_identity() == (
        "active_module",
        "2026.08",
    )


def test_configured_knowledge_base_identity_preserves_registry_without_active_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: SettingsWrapper,
) -> None:
    settings.LX_DTYPES_KB_REGISTRY = str(tmp_path / "registry.json")
    settings.LOOKUP_DTYPES_MODULE_NAME = "legacy_module"
    settings.LOOKUP_DTYPES_MODULE_VERSION = "legacy_version"
    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.active_terminology_selection",
        lambda: None,
    )

    assert get_configured_knowledge_base_identity() is None
