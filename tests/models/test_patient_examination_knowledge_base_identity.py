from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import Client
from lx_dtypes.knowledge_bases import (
    BUILTIN_KNOWLEDGE_BASE_PROVIDER,
    get_packaged_knowledge_base,
)
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.interface.KnowledgeBaseResolver import (
    clear_knowledge_base_resolver_caches,
    load_module_config,
)
from pytest_django.fixtures import SettingsWrapper

from endoreg_db.models import Examination, Patient, PatientExamination


def _write_packaged_registry(
    path: Path,
    *,
    active: KnowledgeBaseIdentity,
) -> None:
    descriptors = [
        get_packaged_knowledge_base("dgvs_reporting"),
        get_packaged_knowledge_base("star_upper_gi"),
        get_packaged_knowledge_base("mst_3_0"),
    ]
    modules = {
        descriptor.module_name: {
            descriptor.version: {
                "sources": [
                    {
                        "kind": "provider",
                        "provider": BUILTIN_KNOWLEDGE_BASE_PROVIDER,
                        "content_sha256": descriptor.content_sha256,
                    }
                ]
            }
        }
        for descriptor in descriptors
    }
    path.write_text(
        json.dumps(
            {
                "active": {
                    "module_name": active.knowledge_base_module,
                    "version": active.knowledge_base_version,
                },
                "modules": modules,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def packaged_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: SettingsWrapper,
) -> Iterator[Path]:
    path = tmp_path / "knowledge_base_registry.json"
    _write_packaged_registry(
        path,
        active=KnowledgeBaseIdentity(
            knowledge_base_module="star_upper_gi",
            knowledge_base_version="0.1.2",
        ),
    )
    settings.LX_DTYPES_KB_REGISTRY = str(path)
    monkeypatch.setenv("LX_DTYPES_KB_REGISTRY", str(path))
    clear_knowledge_base_resolver_caches()
    yield path
    clear_knowledge_base_resolver_caches()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("module_name", "version"),
    [("dgvs_reporting", ""), ("", "0.1.0")],
)
def test_patient_examination_rejects_partial_identity(
    module_name: str,
    version: str,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"kb-partial-{module_name}-{version}",
        first_name="KB",
        last_name="Partial",
    )

    with pytest.raises(DjangoValidationError, match="must be set together"):
        PatientExamination.objects.create(
            patient=patient,
            knowledge_base_module=module_name,
            knowledge_base_version=version,
        )


@pytest.mark.django_db
def test_persisted_identity_beats_changed_active_knowledge_base(
    packaged_registry: Path,
) -> None:
    selected = KnowledgeBaseIdentity(
        knowledge_base_module="dgvs_reporting",
        knowledge_base_version="0.1.0",
    )
    patient = Patient.objects.create(
        patient_hash="kb-persistence-patient",
        first_name="KB",
        last_name="Persistence",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module=selected.knowledge_base_module,
        knowledge_base_version=selected.knowledge_base_version,
    )
    patient_examination_id = patient_examination.pk

    del patient_examination
    reloaded = PatientExamination.objects.get(pk=patient_examination_id)
    reconstructed = KnowledgeBaseIdentity(
        knowledge_base_module=reloaded.knowledge_base_module,
        knowledge_base_version=reloaded.knowledge_base_version,
    )
    assert reconstructed == selected
    resolved = load_module_config(
        reconstructed.knowledge_base_module,
        version=reconstructed.knowledge_base_version,
    )
    assert resolved.knowledge_base_identity == selected

    _write_packaged_registry(
        packaged_registry,
        active=KnowledgeBaseIdentity(
            knowledge_base_module="mst_3_0",
            knowledge_base_version="3.0.0",
        ),
    )
    clear_knowledge_base_resolver_caches()

    reloaded.refresh_from_db()
    resolved_after_default_change = load_module_config(
        reloaded.knowledge_base_module,
        version=reloaded.knowledge_base_version,
    )
    assert resolved_after_default_change.knowledge_base_identity == selected


@pytest.mark.django_db
def test_patient_examination_api_round_trip_preserves_frontend_identity(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    user = User.objects.create_user(username="kb-api-user", is_staff=True)
    client = Client()
    client.force_login(user)
    patient = Patient.objects.create(
        patient_hash="kb-api-patient",
        first_name="KB",
        last_name="API",
    )
    examination = Examination.objects.create(name="kb-api-examination")
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination,
    )

    options_response = client.get("/dtypes-api/terminology/bundles", secure=True)
    assert options_response.status_code == 200, options_response.content
    available_identities = {
        (bundle["module_name"], bundle["version"])
        for bundle in options_response.json()["bundles"]
    }
    assert ("star_upper_gi", "0.1.2") in available_identities
    assert ("dgvs_reporting", "0.1.0") in available_identities

    response = client.patch(
        f"/api/patient-examinations/{patient_examination.pk}/",
        data=json.dumps(
            {
                "knowledge_base_module": "dgvs_reporting",
                "knowledge_base_version": "0.1.0",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    assert response.json()["knowledge_base_module"] == "dgvs_reporting"
    assert response.json()["knowledge_base_version"] == "0.1.0"

    patient_examination.refresh_from_db()
    assert patient_examination.knowledge_base_module == "dgvs_reporting"
    assert patient_examination.knowledge_base_version == "0.1.0"

    reloaded_response = client.get(
        f"/api/patient-examinations/{patient_examination.pk}/"
    )
    assert reloaded_response.status_code == 200, reloaded_response.content
    assert reloaded_response.json()["knowledge_base_module"] == "dgvs_reporting"
    assert reloaded_response.json()["knowledge_base_version"] == "0.1.0"

    resolved = load_module_config(
        patient_examination.knowledge_base_module,
        version=patient_examination.knowledge_base_version,
    )
    assert resolved.knowledge_base_identity == KnowledgeBaseIdentity(
        knowledge_base_module="dgvs_reporting",
        knowledge_base_version="0.1.0",
    )


@pytest.mark.django_db
def test_patient_examination_api_rejects_unknown_identity(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    user = User.objects.create_user(username="kb-api-invalid", is_staff=True)
    client = Client()
    client.force_login(user)
    patient = Patient.objects.create(
        patient_hash="kb-api-invalid-patient",
        first_name="KB",
        last_name="Invalid",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    response = client.patch(
        f"/api/patient-examinations/{patient_examination.pk}/",
        data=json.dumps(
            {
                "knowledge_base_module": "unknown_frontend_module",
                "knowledge_base_version": "999.0",
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 400, response.content
    patient_examination.refresh_from_db()
    assert patient_examination.knowledge_base_module == ""
    assert patient_examination.knowledge_base_version == ""
