# pyright: reportPrivateUsage=false
import builtins
import importlib
from importlib.util import find_spec
import sys
import subprocess
from typing import Any

from django.conf import settings
import pytest
from pytest import MonkeyPatch

from endoreg_db.apps import EndoregDbConfig


@pytest.mark.unit
@pytest.mark.no_db
def test_upload_job_file_import_without_django_typing_patch() -> None:
    from endoreg_db.models.hub import upload_job_file

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import runpy
import sys
import django
from django.conf import settings
from django.db import models

settings.configure(INSTALLED_APPS=["django.contrib.contenttypes"])
django.setup()
assert not hasattr(models.CharField, "__class_getitem__")
# Load the actual model under an installed minimal app, without endoreg settings
# or their django_stubs_ext monkeypatch. No database connection is needed.
namespace = runpy.run_path(
    sys.argv[1], run_name="django.contrib.contenttypes.inventory_regression"
)
model = namespace["UploadJobFile"]
assert model._meta.get_field("path").max_length == 2048
assert model._meta.get_field("upload_job").remote_field.model == "UploadJob"
""",
            str(upload_job_file.__file__),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
def test_runtime_reconciliation_receiver_accepts_django_signal_kwargs(
    monkeypatch: MonkeyPatch,
) -> None:
    from django.dispatch import Signal
    from django.test import override_settings

    import endoreg_db.apps as apps_module
    import endoreg_db.services.runtime.reconciliation as reconciliation
    import endoreg_db.utils.paths as paths_module

    signal = Signal()
    calls: list[bool] = []
    monkeypatch.setattr(apps_module, "connection_created", signal)
    monkeypatch.setattr(apps_module, "ensure_keycloak_settings", lambda: None)
    monkeypatch.setattr(paths_module, "validate_runtime_storage_contract", lambda: None)
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver"])
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    def mock_run_once(self: object) -> None:
        calls.append(True)

    monkeypatch.setattr(
        reconciliation.ReconciliationService,
        "run_once",
        mock_run_once,
    )
    config = EndoregDbConfig("endoreg_db", importlib.import_module("endoreg_db"))
    with override_settings(DEBUG=True):
        config.ready()
        signal.send(sender=object, connection=object(), future_argument=True)  # pyright: ignore[reportUnknownMemberType]
    assert calls == [True]


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
        if name == "endoreg_db.services.runtime.reconciliation":
            raise AssertionError(
                "reconciliation must not be imported during pytest startup"
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    config = EndoregDbConfig("endoreg_db", importlib.import_module("endoreg_db"))

    config.ready()
