"""Business service layer for endoreg_db.

The package root stays intentionally light. Import concrete behavior from the
domain module that owns it, or use the selected compatibility exports below.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "build_preanonymized_payload": (
        ".imports.tabular_import_formats",
        "build_preanonymized_payload",
    ),
    "convert_sap_ish_zip_to_preanonymized_drop": (
        ".interoperability.sap_ish_import",
        "convert_sap_ish_zip_to_preanonymized_drop",
    ),
    "convert_sap_ish_txt_directory_to_preanonymized_drop": (
        ".interoperability.sap_ish_import",
        "convert_sap_ish_txt_directory_to_preanonymized_drop",
    ),
    "persist_sap_ish_clinical_rows": (
        ".interoperability.sap_ish_clinical",
        "persist_sap_ish_clinical_rows",
    ),
    "load_document_templates": (
        ".imports.tabular_import_formats",
        "load_document_templates",
    ),
    "normalize_document_row": (
        ".imports.tabular_import_formats",
        "normalize_document_row",
    ),
    "resolve_document_template": (
        ".imports.tabular_import_formats",
        "resolve_document_template",
    ),
}

__all__ = [
    "build_preanonymized_payload",
    "convert_sap_ish_txt_directory_to_preanonymized_drop",
    "convert_sap_ish_zip_to_preanonymized_drop",
    "load_document_templates",
    "normalize_document_row",
    "persist_sap_ish_clinical_rows",
    "resolve_document_template",
]

if TYPE_CHECKING:
    from endoreg_db.services.interoperability.sap_ish_import import (
        convert_sap_ish_txt_directory_to_preanonymized_drop,
        convert_sap_ish_zip_to_preanonymized_drop,
    )
    from endoreg_db.services.interoperability.sap_ish_clinical import (
        persist_sap_ish_clinical_rows,
    )
    from endoreg_db.services.imports.tabular_import_formats import (
        build_preanonymized_payload,
        load_document_templates,
        normalize_document_row,
        resolve_document_template,
    )


def __getattr__(name: str) -> Any:
    export_path = _EXPORTS.get(name)
    if export_path is not None:
        module_name, attribute_name = export_path
        module = import_module(module_name, __name__)
        value = getattr(module, attribute_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
