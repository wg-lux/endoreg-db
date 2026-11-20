"""SensitiveMeta dictionary ingestion lives in patient_identity without changing identity."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone

import endoreg_db.models.metadata.sensitive_meta_logic as legacy_logic
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.patient_identity import sensitive_meta_ingestion
from endoreg_db.utils.hashs import (
    get_patient_examination_hash,
    get_patient_identity_fingerprint,
)

_MOVED_PUBLIC_NAMES = (
    "create_sensitive_meta_from_dict",
    "update_sensitive_meta_from_dict",
    "update_or_create_sensitive_meta_from_dict",
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _module_level_imports(relative_path: str) -> list[str]:
    tree = ast.parse((_PROJECT_ROOT / relative_path).read_text())
    modules: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


@pytest.mark.no_db
@pytest.mark.parametrize("name", _MOVED_PUBLIC_NAMES)
def test_legacy_module_path_resolves_to_the_service_function(name: str) -> None:
    assert getattr(legacy_logic, name) is getattr(sensitive_meta_ingestion, name)


@pytest.mark.no_db
@pytest.mark.parametrize("name", ["_selected_model_data", "parse_any_dates", ""])
def test_legacy_module_does_not_expose_moved_private_or_unknown_names(
    name: str,
) -> None:
    with pytest.raises(AttributeError):
        getattr(legacy_logic, name)


@pytest.mark.no_db
def test_model_logic_does_not_import_the_service_at_module_level() -> None:
    imports = _module_level_imports(
        "endoreg_db/models/metadata/sensitive_meta_logic.py"
    )
    assert not [module for module in imports if "patient_identity" in module]


@pytest.mark.no_db
def test_ingestion_service_uses_leaf_model_imports_only() -> None:
    imports = _module_level_imports(
        "endoreg_db/services/patient_identity/sensitive_meta_ingestion.py"
    )
    assert "endoreg_db.models" not in imports
    assert not [
        module for module in imports if module.startswith("endoreg_db.services")
    ]


def _payload(center: Center, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "center": center,
        "patient_first_name": "Erika",
        "patient_last_name": "Musterfrau",
        "patient_dob": date(1961, 7, 24),
        "patient_gender": "female",
        "examination_date": "2024-05-02",
        "casenumber": "CASE-INGEST",
    }
    payload.update(overrides)
    return payload


def _expected_hashes(center: Center) -> tuple[str, str]:
    patient_hash = get_patient_identity_fingerprint(
        first_name="Erika",
        last_name="Musterfrau",
        dob=date(1961, 7, 24),
        center_key=center.center_key,
    )
    examination_hash = get_patient_examination_hash(
        first_name="Erika",
        last_name="Musterfrau",
        dob=date(1961, 7, 24),
        examination_date=date(2024, 5, 2),
        center_key=center.center_key,
    )
    return patient_hash, examination_hash


@pytest.fixture
def center() -> Center:
    return Center.objects.create(name=f"ingestion-{uuid4().hex}")


@pytest.mark.django_db
def test_model_facade_create_produces_canonical_identity_hashes(
    center: Center,
) -> None:
    sensitive_meta = SensitiveMeta.create_from_dict(_payload(center))

    assert (sensitive_meta.patient_hash, sensitive_meta.examination_hash) == (
        _expected_hashes(center)
    )


@pytest.mark.django_db
def test_update_or_create_is_idempotent_and_keeps_identity(center: Center) -> None:
    created, was_created = (
        sensitive_meta_ingestion.update_or_create_sensitive_meta_from_dict(
            SensitiveMeta, _payload(center)
        )
    )
    updated, was_created_again = (
        sensitive_meta_ingestion.update_or_create_sensitive_meta_from_dict(
            SensitiveMeta, _payload(center), instance=created
        )
    )

    assert (was_created, was_created_again) == (True, False)
    assert updated.pk == created.pk
    assert SensitiveMeta.objects.filter(pk=created.pk).count() == 1
    assert (updated.patient_hash, updated.examination_hash) == _expected_hashes(center)


@pytest.mark.django_db
@pytest.mark.parametrize("entry_point", ["legacy_module", "service", "facade"])
def test_every_entry_point_creates_the_same_identity(
    center: Center, entry_point: str
) -> None:
    if entry_point == "legacy_module":
        create = getattr(legacy_logic, "create_sensitive_meta_from_dict")
        sensitive_meta = create(SensitiveMeta, _payload(center))
    elif entry_point == "service":
        sensitive_meta = sensitive_meta_ingestion.create_sensitive_meta_from_dict(
            SensitiveMeta, _payload(center)
        )
    else:
        sensitive_meta = SensitiveMeta.create_from_dict(_payload(center))

    assert (sensitive_meta.patient_hash, sensitive_meta.examination_hash) == (
        _expected_hashes(center)
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "unusable_update",
    [
        {"patient_dob": "not a date"},
        {"center_name": "no-such-center"},
        {"center": "not-a-center-instance"},
    ],
)
def test_unusable_update_values_keep_the_persisted_identity(
    center: Center, unusable_update: dict[str, object]
) -> None:
    # Pins the pre-extraction behaviour: unusable values are logged and skipped.
    # Changing this to fail loudly is a separate, reviewed behaviour change.
    sensitive_meta = SensitiveMeta.create_from_dict(_payload(center))
    identity_fields = (
        "center_id",
        "patient_first_name",
        "patient_dob",
        "patient_hash",
        "examination_hash",
    )
    before = SensitiveMeta.objects.values(*identity_fields).get(pk=sensitive_meta.pk)

    sensitive_meta.update_from_dict(unusable_update)

    after = SensitiveMeta.objects.values(*identity_fields).get(pk=sensitive_meta.pk)
    assert after == before


@pytest.mark.django_db
@pytest.mark.parametrize("iso_dob", ["1961-07-04", "1961-07-24"])
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-existing: the create path parses ISO dates of birth with dateparser "
        "DATE_ORDER=DMY. A day of 12 or less swaps day and month; a larger day "
        "drops the date of birth and the patient hash. The update path and "
        "examination dates parse ISO correctly."
    ),
)
def test_create_keeps_an_iso_date_of_birth(center: Center, iso_dob: str) -> None:
    sensitive_meta = SensitiveMeta.create_from_dict(
        _payload(center, patient_dob=iso_dob)
    )

    persisted = SensitiveMeta.objects.get(pk=sensitive_meta.pk)
    assert persisted.patient_dob is not None
    assert timezone.localtime(persisted.patient_dob).date() == date.fromisoformat(
        iso_dob
    )
