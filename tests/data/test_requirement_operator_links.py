from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
REQUIREMENT_DIR = ROOT_DIR / "endoreg_db" / "data" / "requirement"
OPERATOR_DIR = ROOT_DIR / "endoreg_db" / "data" / "requirement_operator"


def _load_yaml_entries(directory: Path) -> Iterable[Tuple[Path, dict]]:
    for path in sorted(directory.glob("*.yaml")):
        content = yaml.safe_load(path.read_text()) or []
        if not isinstance(content, list):
            raise AssertionError(f"Expected a list of entries in {path}, got {type(content)!r}")
        for entry in content:
            if not isinstance(entry, dict):
                raise AssertionError(f"Expected each entry to be a mapping in {path}, got {type(entry)!r}")
            yield path, entry


def _gather_requirement_fields() -> List[Tuple[Path, dict]]:
    requirement_fields: List[Tuple[Path, dict]] = []
    for path, entry in _load_yaml_entries(REQUIREMENT_DIR):
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            raise AssertionError(f"Requirement entry in {path} is missing a 'fields' mapping")
        requirement_fields.append((path, fields))
    return requirement_fields


def _gather_operator_names() -> set[str]:
    operator_names: set[str] = set()
    for path, entry in _load_yaml_entries(OPERATOR_DIR):
        fields = entry.get("fields")
        if not isinstance(fields, dict):
            raise AssertionError(f"Requirement operator entry in {path} is missing a 'fields' mapping")
        name = fields.get("name")
        if not isinstance(name, str) or not name:
            raise AssertionError(f"Requirement operator entry in {path} is missing a valid name")
        operator_names.add(name)
    return operator_names


REQUIREMENT_FIELDS = _gather_requirement_fields()
REQUIREMENT_IDS = [f"{path.name}:{fields.get('name', '<unnamed>')}" for path, fields in REQUIREMENT_FIELDS]
OPERATOR_NAMES = _gather_operator_names()


@pytest.mark.parametrize("path, fields", REQUIREMENT_FIELDS, ids=REQUIREMENT_IDS)
def test_requirements_have_at_least_one_operator(path: Path, fields: dict) -> None:
    operators = fields.get("operators")
    assert operators, f"Requirement '{fields.get('name')}' in {path.name} must define at least one operator"
    if not isinstance(operators, list):
        raise AssertionError(f"Requirement '{fields.get('name')}' in {path.name} must use a list for 'operators', got {type(operators)!r}")
    for operator in operators:
        if not isinstance(operator, str) or not operator.strip():
            raise AssertionError(f"Requirement '{fields.get('name')}' in {path.name} contains an invalid operator entry: {operator!r}")


@pytest.mark.parametrize("path, fields", REQUIREMENT_FIELDS, ids=REQUIREMENT_IDS)
def test_requirement_operators_reference_existing_entries(path: Path, fields: dict) -> None:
    missing = [operator for operator in fields.get("operators", []) if operator not in OPERATOR_NAMES]
    assert not missing, f"Requirement '{fields.get('name')}' in {path.name} references unknown operators: {', '.join(missing)}"
