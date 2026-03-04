from __future__ import annotations
import sys
from pathlib import Path


def _prefer_repo_lx_dtypes() -> None:
    """
    Ensure the repository's `lx-data-models` submodule is imported before any
    globally installed `lx_dtypes` package.

    This keeps the lookup contract source (`lx_dtypes`) in lockstep with the
    checked-out `endoreg_db` codebase.
    """
    repo_root = Path(__file__).resolve().parents[2]
    submodule_root = repo_root / "lx-data-models"
    if not submodule_root.exists():
        return
    submodule_root_str = str(submodule_root)
    if submodule_root_str in sys.path:
        sys.path.remove(submodule_root_str)
    sys.path.insert(0, submodule_root_str)


_prefer_repo_lx_dtypes()

try:
    from lx_dtypes.models.knowledge_base.report_template import (
        LEGACY_LOOKUP_KEY_MAP,
        LookupDerivedUpdatesDataDict,
        LookupInitRequest,
        LookupRecomputeResponseDataDict,
        LookupPartsPatchRequest,
        LookupPartsResponse,
        LookupRecomputeResponse,
        LookupState,
        LookupStateDataDict,
        RequirementSetSummary,
        ValidationError,
        build_lookup_recompute_response,
        normalize_lookup_keys,
        validate_lookup_parts_response,
        validate_lookup_state,
        validate_lookup_updates,
    )
except Exception as exc:
    raise ImportError(
        "Failed to import lookup-state contracts from lx_dtypes. "
        "Expected a compatible repo-local lx-data-models checkout with "
        "lx_dtypes.models.knowledge_base.report_template.LookupState exports."
    ) from exc

__all__ = [
    "LEGACY_LOOKUP_KEY_MAP",
    "LookupDerivedUpdatesDataDict",
    "LookupInitRequest",
    "LookupPartsPatchRequest",
    "LookupPartsResponse",
    "LookupRecomputeResponse",
    "LookupRecomputeResponseDataDict",
    "LookupState",
    "LookupStateDataDict",
    "RequirementSetSummary",
    "ValidationError",
    "build_lookup_recompute_response",
    "normalize_lookup_keys",
    "validate_lookup_parts_response",
    "validate_lookup_state",
    "validate_lookup_updates",
]
