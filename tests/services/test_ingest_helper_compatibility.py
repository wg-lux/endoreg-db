"""Historical imports remain usable while helper ownership stays unambiguous."""

from __future__ import annotations

import ast
import inspect
from types import ModuleType

import pytest

from endoreg_db.services.hub import ingest, ingest_provenance, preanonymized_metadata


@pytest.mark.parametrize(
    ("owner", "names"),
    [
        (
            ingest_provenance,
            (
                "UploadProvenance",
                "_upload_provenance",
                "_update_upload_provenance",
                "_normalized_upload_provenance",
                "_get_upload_provenance",
                "record_active_learning_selection_provenance",
            ),
        ),
        (
            preanonymized_metadata,
            (
                "_attach_external_id_to_sensitive_meta",
                "_normalize_sensitive_meta_payload",
                "_apply_preanonymized_metadata",
            ),
        ),
    ],
)
def test_historical_helper_imports_are_the_canonical_objects(
    owner: ModuleType,
    names: tuple[str, ...],
) -> None:
    for name in names:
        canonical = getattr(owner, name.lstrip("_"))
        assert getattr(ingest, name) is canonical
        assert canonical.__module__ == owner.__name__


@pytest.mark.parametrize("owner", [ingest_provenance, preanonymized_metadata])
def test_helper_modules_do_not_import_the_orchestrator(owner: ModuleType) -> None:
    for node in ast.walk(ast.parse(inspect.getsource(owner))):
        if isinstance(node, ast.Import):
            assert all(alias.name != ingest.__name__ for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in {"ingest", ingest.__name__}
            if node.module in {None, "endoreg_db.services.hub"}:
                assert all(alias.name != "ingest" for alias in node.names)
