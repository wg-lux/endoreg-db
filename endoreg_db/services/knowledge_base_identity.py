from __future__ import annotations

from pathlib import Path

from django.conf import settings
from lx_dtypes.django.api.terminology_routes import active_terminology_selection
from lx_dtypes.models.interface.KnowledgeBaseResolver import (
    get_knowledge_base_identity,
)


def _resolve_dtypes_data_root() -> Path | None:
    configured_root = str(getattr(settings, "LOOKUP_DTYPES_DATA_ROOT", "")).strip()
    if not configured_root:
        return None
    configured_path = Path(configured_root).expanduser().resolve()
    return configured_path if configured_path.exists() else None


def get_configured_knowledge_base_module() -> str:
    module_name = str(
        getattr(settings, "LOOKUP_DTYPES_MODULE_NAME", "report_template_examples")
    ).strip()
    return module_name or "report_template_examples"


def get_configured_knowledge_base_version() -> str | None:
    version = str(getattr(settings, "LOOKUP_DTYPES_MODULE_VERSION", "")).strip()
    return version or None


def get_configured_knowledge_base_identity() -> tuple[str, str] | None:
    registry_path = str(getattr(settings, "LX_DTYPES_KB_REGISTRY", "")).strip()
    if registry_path:
        active_identity = active_terminology_selection()
        if active_identity is None:
            return None
        module_name, version = active_identity
        return get_knowledge_base_identity(module_name, version=version)

    module_name = get_configured_knowledge_base_module()
    configured_version = get_configured_knowledge_base_version()
    data_root = _resolve_dtypes_data_root()
    if configured_version is not None:
        if data_root is None:
            return module_name, configured_version
        return get_knowledge_base_identity(
            module_name,
            version=configured_version,
            input_dirs=[data_root],
        )
    if data_root is None:
        return None

    return get_knowledge_base_identity(module_name, input_dirs=[data_root])


__all__ = [
    "get_configured_knowledge_base_identity",
    "get_configured_knowledge_base_module",
    "get_configured_knowledge_base_version",
]
