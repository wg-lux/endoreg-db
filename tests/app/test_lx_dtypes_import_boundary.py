import importlib
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_lx_dtypes_is_declared_package_dependency():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    dependencies = pyproject["project"]["dependencies"]

    assert any(
        dependency.lower().replace("_", "-").startswith("lx-dtypes")
        for dependency in dependencies
    )


def test_urlconf_does_not_add_lx_data_models_checkout_to_sys_path(monkeypatch):
    checkout_roots = {
        str((PROJECT_ROOT / "lx-data-models").resolve()),
        str((PROJECT_ROOT.parent / "lx-data-models").resolve()),
    }
    monkeypatch.setattr(
        sys,
        "path",
        [path_entry for path_entry in sys.path if path_entry not in checkout_roots],
    )

    urls_module = sys.modules.get("endoreg_db.urls")
    if urls_module is None:
        importlib.import_module("endoreg_db.urls")
    else:
        importlib.reload(urls_module)

    assert checkout_roots.isdisjoint(sys.path)
