from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import re
import tomllib
from typing import cast

from packaging.requirements import Requirement
from pydantic import BaseModel

from lx_dtypes.models.ledger import ledger_models_lookup
from lx_dtypes.models.ledger.medical import (
    PatientDisease,
    PatientEvent,
    PatientLabSample,
    PatientLabValue,
    PatientMedicalLedger,
    PatientMedication,
    PatientMedicationSchedule,
    l_medical_lookup,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_MEDICAL_MODELS = frozenset(
    {
        "PatientDisease",
        "PatientEvent",
        "PatientLabSample",
        "PatientLabValue",
        "PatientMedication",
        "PatientMedicationSchedule",
        "PatientMedicalLedger",
    }
)
MEDICAL_MODELS: tuple[tuple[str, type[BaseModel]], ...] = (
    ("PatientDisease", PatientDisease),
    ("PatientEvent", PatientEvent),
    ("PatientLabSample", PatientLabSample),
    ("PatientLabValue", PatientLabValue),
    ("PatientMedication", PatientMedication),
    ("PatientMedicationSchedule", PatientMedicationSchedule),
    ("PatientMedicalLedger", PatientMedicalLedger),
)
SNAKE_CASE_FIELD = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _lx_dtypes_requirement() -> Requirement:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    dependencies = cast(list[str], pyproject["project"]["dependencies"])
    return next(
        Requirement(dependency)
        for dependency in dependencies
        if Requirement(dependency).name == "lx-dtypes"
    )


def _locked_lx_dtypes_version() -> str:
    lock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text())
    packages = cast(list[dict[str, object]], lock["package"])
    package = next(entry for entry in packages if entry.get("name") == "lx-dtypes")
    return cast(str, package["version"])


def _schema_property_names(schema: dict[str, object]) -> set[str]:
    names: set[str] = set()
    pending: list[object] = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            properties = mapping.get("properties")
            if isinstance(properties, dict):
                names.update(cast(dict[str, object], properties))
            pending.extend(mapping.values())
        elif isinstance(value, list):
            pending.extend(cast(list[object], value))
    return names


def test_installed_lx_dtypes_matches_declared_and_locked_version() -> None:
    installed_version = version("lx-dtypes")
    requirement = _lx_dtypes_requirement()

    assert installed_version == _locked_lx_dtypes_version()
    assert installed_version in requirement.specifier


def test_all_medical_models_have_public_closed_snake_case_schemas() -> None:
    assert frozenset(l_medical_lookup) == EXPECTED_MEDICAL_MODELS

    for model_name, model in MEDICAL_MODELS:
        assert l_medical_lookup[model_name] is model
        assert ledger_models_lookup[model_name] is model
        schema = model.model_json_schema()
        assert schema["additionalProperties"] is False
        assert all(
            SNAKE_CASE_FIELD.fullmatch(field_name)
            for field_name in _schema_property_names(schema)
        )
