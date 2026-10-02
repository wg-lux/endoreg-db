"""Falllebenszyklus, Dokumentzuordnung und Untersuchungsbeziehungen."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .lifecycle import (
        CASE_RELATION_FIELDS as CASE_RELATION_FIELDS,
        CaseLifecycleError as CaseLifecycleError,
        close_case as close_case,
        persist_case_graph as persist_case_graph,
        reopen_case as reopen_case,
        validate_case_relationships as validate_case_relationships,
    )


def __getattr__(name: str) -> object:
    module = import_module(".lifecycle", __name__)
    if name == "__all__":
        return [
            attribute for attribute in vars(module) if not attribute.startswith("_")
        ]
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(import_module(".lifecycle", __name__))))
