from __future__ import annotations

import json
from io import StringIO

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
    assert "storage" in payload["paths"]
    assert "import_dir" in payload["paths"]


def test_validate_runtime_storage_contract_emits_text_contract() -> None:
    output = StringIO()

    call_command("validate_runtime_storage_contract", stdout=output)

    rendered = output.getvalue()
    assert "Runtime root:" in rendered
    assert "- storage:" in rendered


def test_validate_runtime_storage_contract_reports_protected_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()

    def reject_storage() -> None:
        raise RuntimeError("storage: outside runtime root")

    monkeypatch.setattr(
        command_module, "validate_runtime_storage_contract", reject_storage
    )

    with pytest.raises(CommandError, match="Runtime storage contract is invalid"):
        call_command("validate_runtime_storage_contract", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["valid"] is False
    assert payload["violations"] == ["storage: outside runtime root"]
