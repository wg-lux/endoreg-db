"""Keep service ownership visible and validate relocated import entry points."""

from importlib import import_module
from pathlib import Path
from typing import cast

import pytest
import yaml


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
