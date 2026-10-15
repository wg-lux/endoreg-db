"""Keep service ownership visible and validate relocated import entry points."""

from importlib import import_module
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest
import yaml

pytestmark = pytest.mark.no_db


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = PROJECT_ROOT / "endoreg_db" / "services"


def _module_moves() -> dict[str, str]:
    document = cast(
        dict[str, object], yaml.safe_load((SERVICES_ROOT / "structure.yml").read_text())
    )
    moves = cast(dict[str, str], document["module_moves"])
    assert all(
        isinstance(old, str) and isinstance(new, str) for old, new in moves.items()
    )
    return moves


def test_service_root_contains_only_package_initialization() -> None:
    assert {path.name for path in SERVICES_ROOT.glob("*.py")} == {"__init__.py"}


def test_service_overview_covers_every_domain() -> None:
    document = cast(
        dict[str, object], yaml.safe_load((SERVICES_ROOT / "structure.yml").read_text())
    )
    domains = cast(dict[str, object], document["domains"])
    packages = {
        path.name
        for path in SERVICES_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    assert set(domains) == packages


@pytest.mark.parametrize("old_path,new_path", sorted(_module_moves().items()))
def test_relocated_service_imports(old_path: str, new_path: str) -> None:
    assert not (PROJECT_ROOT / old_path).exists()
    assert (PROJECT_ROOT / new_path).is_file()
    module_name = new_path.removesuffix(".py").replace("/", ".")
    assert import_module(module_name).__name__ == module_name


@pytest.mark.parametrize("old_path,new_path", sorted(_module_moves().items()))
def test_legacy_service_imports_preserve_module_identity(
    old_path: str, new_path: str
) -> None:
    old_name = old_path.removesuffix(".py").replace("/", ".")
    new_name = new_path.removesuffix(".py").replace("/", ".")
    legacy = import_module(old_name)
    canonical = import_module(new_name)
    root = import_module("endoreg_db.services")
    assert getattr(root, old_name.rsplit(".", 1)[1]) is legacy
    if old_name == "endoreg_db.services.cases":
        for name, value in vars(canonical).items():
            if not name.startswith("_"):
                assert getattr(legacy, name) is value
        assert import_module("endoreg_db.services.cases.documents")
    else:
        assert legacy is canonical
        assert legacy.__name__ == new_name
        assert legacy.__spec__ is not None
        assert legacy.__spec__.name == new_name

    # Exercise actual import statements, including imports of module symbols.
    namespace: dict[str, object] = {}
    exec(
        f"from endoreg_db.services import {old_name.rsplit('.', 1)[1]} as service",
        namespace,
    )
    assert namespace["service"] is legacy
    exec(f"from {old_name} import *", namespace)
    canonical_namespace: dict[str, object] = {}
    exec(f"from {new_name} import *", canonical_namespace)
    for name, value in canonical_namespace.items():
        if name != "__builtins__":
            assert namespace[name] is value


def test_legacy_aliases_are_lazy_and_keep_reload_metadata() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys
import endoreg_db.services as services
assert not any(name.startswith('endoreg_db.services.') for name in sys.modules)
assert 'endoreg_db.services.imports.execution' not in sys.modules
finder_count = sum(type(item).__name__ == '_LegacyServiceFinder' for item in sys.meta_path)
importlib.reload(services)
assert sum(type(item).__name__ == '_LegacyServiceFinder' for item in sys.meta_path) == finder_count == 1
from endoreg_db.services import import_execution
from endoreg_db.services.import_execution import ImportExecutionFence
from endoreg_db.services.imports import execution
assert import_execution is execution
assert ImportExecutionFence is execution.ImportExecutionFence
assert import_execution.__spec__.name == 'endoreg_db.services.imports.execution'
assert importlib.reload(import_execution) is execution
try:
    importlib.import_module('endoreg_db.services.nonexistent_service')
except ModuleNotFoundError:
    pass
else:
    raise AssertionError('Unknown modules must fail explicitly')
""",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
