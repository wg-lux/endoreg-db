# pyright: reportPrivateUsage=false
import builtins
import importlib
from importlib.util import find_spec
import sys
from typing import Any

from django.conf import settings
import pytest
from pytest import MonkeyPatch

from endoreg_db.apps import EndoregDbConfig


@pytest.mark.unit
def test_root_urlconf_has_one_canonical_module() -> None:
    assert settings.ROOT_URLCONF == "endoreg_db.root_urls"
    assert find_spec("endoreg_db.urls.root_urls") is None


@pytest.mark.unit
def test_ninja_namespace_release_supports_registry_free_versions(
    monkeypatch: MonkeyPatch,
) -> None:
    from ninja import NinjaAPI

    from endoreg_db.urls import _release_ninja_api_namespace

    monkeypatch.delattr(NinjaAPI, "_registry", raising=False)

    _release_ninja_api_namespace("endoreg-db-api")


@pytest.mark.unit
def test_ninja_namespace_release_cleans_legacy_registry(
    monkeypatch: MonkeyPatch,
) -> None:
    from ninja import NinjaAPI

    from endoreg_db.urls import _release_ninja_api_namespace

    registry: list[object] = ["endoreg-db-api", "other", "endoreg-db-api"]
    monkeypatch.setattr(NinjaAPI, "_registry", registry, raising=False)

    _release_ninja_api_namespace("endoreg-db-api")

    assert registry == ["other"]


@pytest.mark.unit
def test_app_ready_does_not_import_reconciliation_for_pytest(
    monkeypatch: MonkeyPatch,
) -> None:
    import endoreg_db.apps as apps_module
    import endoreg_db.utils.paths as paths_module

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

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "endoreg_db.services.reconciliation":
            raise AssertionError(
                "reconciliation must not be imported during pytest startup"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    config = EndoregDbConfig("endoreg_db", importlib.import_module("endoreg_db"))

    config.ready()
