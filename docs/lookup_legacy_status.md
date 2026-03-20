# Lookup Workflow Legacy Status

This document records the retirement of the legacy lookup-session workflow.

## Retired

The following codepaths and endpoints are no longer part of the supported backend contract:

- `endoreg_db/views/requirement/lookup.py`
- `endoreg_db/services/lookup_store.py`
- `endoreg_db/schemas/lookup_state.py`
- `POST /api/lookup/init/`
- `GET /api/lookup/{token}/all/`
- `GET|PATCH /api/lookup/{token}/parts/`
- `POST /api/lookup/{token}/recompute/`
- `POST /api/lookup/recompute/`

## Active

- `endoreg_db/views/requirement/evaluate.py`
  - `/api/evaluate-requirements/` evaluates a persisted `patient_examination_id`
- `endoreg_db/services/lookup_service.py`
  - retained as a compatibility import path for persisted requirement guidance only
  - no token/session/cache responsibilities remain
- `GET|PUT /api/patient-examinations/{pk}/draft/`
  - draft persistence for frontend-owned reporting state

## Current architecture

- frontend owns transient reporting state
- `endoreg_db` persists drafts and finalized relational state
- `lx_dtypes` validates typed knowledge-base and ledger payloads
- final requirement/report validation runs against persisted or explicitly submitted typed state, not lookup sessions
