from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.conf import settings


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_dtypes_data_root() -> Path | None:
    configured_root = str(getattr(settings, "LOOKUP_DTYPES_DATA_ROOT", "")).strip()
    if configured_root:
        configured_path = Path(configured_root).expanduser().resolve()
        if configured_path.exists():
            return configured_path

    repo_data_root = _project_root() / "lx-data-models" / "lx_dtypes" / "data"
    if repo_data_root.exists():
        return repo_data_root

    try:
        import lx_dtypes

        package_data_root = Path(lx_dtypes.__file__).resolve().parent / "data"
        if package_data_root.exists():
            return package_data_root
    except Exception:
        return None

    return None


def get_configured_knowledge_base_module() -> str:
    module_name = str(
        getattr(settings, "LOOKUP_DTYPES_MODULE_NAME", "report_template_examples")
    ).strip()
    return module_name or "report_template_examples"


def get_configured_knowledge_base_version() -> str | None:
    version = str(getattr(settings, "LOOKUP_DTYPES_MODULE_VERSION", "")).strip()
    return version or None


@lru_cache(maxsize=8)
def get_configured_knowledge_base_identity() -> tuple[str, str] | None:
    module_name = get_configured_knowledge_base_module()
    configured_version = get_configured_knowledge_base_version()
    if configured_version is not None:
        return module_name, configured_version

    data_root = _resolve_dtypes_data_root()
    if data_root is None:
        return None

    try:
        from lx_dtypes.models.interface.DataLoader import DataLoader

        loader = DataLoader(input_dirs=[data_root])
        loader.load_module_configs()
        module_config = loader.get_initialized_config(module_name)
    except Exception:
        return None

    return module_config.name, module_config.version


__all__ = [
    "get_configured_knowledge_base_identity",
    "get_configured_knowledge_base_module",
    "get_configured_knowledge_base_version",
]
