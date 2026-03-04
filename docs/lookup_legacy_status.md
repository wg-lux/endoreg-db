# Lookup Workflow Legacy Status

This document declares which lookup-related codepaths are active and which are legacy as of March 4, 2026.

## Active (source of truth)
- `endoreg_db/views/requirement/lookup.py`
  - REST lifecycle contract and error payloads for:
  - `POST /api/lookup/init/`
  - `GET /api/lookup/{token}/all/`
  - `GET|PATCH /api/lookup/{token}/parts/`
  - `POST /api/lookup/{token}/recompute/`
  - `POST /api/lookup/recompute/` (token-less fallback)
- `endoreg_db/services/lookup_service.py`
  - session init/recompute business logic
  - dtypes is primary runtime (`LOOKUP_REQUIREMENT_SOURCE=dtypes` by default)
  - legacy fallback only when `LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED=true`
- `endoreg_db/views/requirement/evaluate.py`
  - `/api/evaluate-requirements/` is dtypes-guidance backed
  - returns normalized `snake_case` meta keys
- `endoreg_db/services/lookup_store.py`
  - cache-backed session storage and validation/recovery
- `endoreg_db/schemas/lookup_state.py`
  - typed lookup contract adapter to `lx_dtypes`
- `docs/frontend_agent_lookup_contract.md`
  - canonical frontend-facing workflow contract

## Legacy (do not extend)
- removed legacy lookup view shims (kept in git history only):
  - `endoreg_db/views/requirement_lookup/lookup.py`
  - `endoreg_db/views/requirement_lookup/lookup_store.py`
  - all lookup behavior is now in `endoreg_db/views/requirement/lookup.py` and `endoreg_db/services/lookup_store.py`
- Requirement graph runtime (legacy requirement DSL):
  - `endoreg_db/models/requirement/*`
  - `endoreg_db/management/commands/load_requirement_data.py`
  - status: compatibility layer while dtypes validator runtime is phased out
  - do not add new product behavior here; add it in dtypes-backed paths
  - `load_requirement_data` is no longer part of default `load_base_db_data`; use `--include-legacy-requirements` explicitly

## Legacy compatibility behavior
- camel_case lookup keys remain accepted only via compatibility normalization (`LEGACY_LOOKUP_KEY_MAP` in `lx_dtypes` adapter path), but snake_case is the only supported contract for new client code.

## Implementation-note docs (non-canonical)
- `docs/tag_management_guide.md`
- `docs/tag_filtering_implementation.md`
- `docs/handoff_frontend_reporting_anonymization.md`

These documents can be useful historical context, but they are not the canonical contract for lookup lifecycle behavior.
