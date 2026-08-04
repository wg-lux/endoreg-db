import builtins
import importlib
import sys

import pytest

from endoreg_db.apps import EndoregDbConfig


@pytest.mark.unit
def test_app_ready_does_not_import_reconciliation_for_pytest(monkeypatch):
    import endoreg_db.apps as apps_module
    import endoreg_db.utils.filesystem.paths as paths_module

    monkeypatch.setattr(apps_module, "ensure_keycloak_settings", lambda: None)
    monkeypatch.setattr(
        paths_module,
        "validate_runtime_storage_contract",
        lambda: None,
        raising=True,
    )
    monkeypatch.setattr(
        sys, "argv", ["pytest", "tests/services/test_video_import_service.py"]
    )

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "endoreg_db.services.reconciliation":
            raise AssertionError(
                "reconciliation must not be imported during pytest startup"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    config = EndoregDbConfig("endoreg_db", importlib.import_module("endoreg_db"))

    config.ready()
