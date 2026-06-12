from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest
from pytest import MonkeyPatch

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
def test_patient_examination_save_stamps_knowledge_base_identity(monkeypatch: MonkeyPatch) -> None:
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


def test_configured_knowledge_base_identity_uses_resolver_input_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    get_configured_knowledge_base_identity.cache_clear()
    configured_root = tmp_path / "configured-data"
    configured_root.mkdir(parents=True)
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

    try:
        assert get_configured_knowledge_base_identity() == (
            "report_template_examples",
            "resolved-1.2.3",
        )
    finally:
        get_configured_knowledge_base_identity.cache_clear()
