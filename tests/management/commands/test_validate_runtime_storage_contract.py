from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.management.commands import (
    validate_runtime_storage_contract as command_module,
)


def test_validate_runtime_storage_contract_emits_valid_json() -> None:
    output = StringIO()

    call_command("validate_runtime_storage_contract", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["valid"] is True
    assert payload["violations"] == []
    assert "storage" in payload["protected_paths"]
    assert "import" in payload["public_paths"]


def test_validate_runtime_storage_contract_emits_text_contract() -> None:
    output = StringIO()

    call_command("validate_runtime_storage_contract", stdout=output)

    rendered = output.getvalue()
    assert "Protected runtime root:" in rendered
    assert "Public data root:" in rendered
    assert "- storage:" in rendered


def test_validate_runtime_storage_contract_reports_protected_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()

    def reject_storage(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate == command_module.STORAGE_DIR:
            raise ValueError("outside protected root")
        return candidate

    monkeypatch.setattr(
        command_module,
        "ensure_within_protected_root",
        reject_storage,
    )

    with pytest.raises(CommandError, match="Runtime storage contract is invalid"):
        call_command("validate_runtime_storage_contract", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["valid"] is False
    assert payload["violations"] == ["storage: outside protected root"]
