# dtypes Lookup Module Entrypoint

This document is retained only as a historical marker.

## Status

The dedicated lookup-session entrypoint has been removed from `endoreg_db`.

Removed:

- `endoreg_db/schemas/lookup_state.py`
- `endoreg_db/services/lookup_store.py`
- `/api/lookup/*`

## Current entrypoints

- `GET|PUT /api/patient-examinations/{pk}/draft/`
  - persists frontend-owned report draft state
- `POST /api/evaluate-requirements/`
  - evaluates advisory requirement guidance for a persisted `patient_examination_id`
- typed ledger/report validation endpoints in `lx_dtypes`
  - validate explicit typed payloads against the knowledge base

## Ownership

- frontend owns transient editor state
- `endoreg_db` owns draft persistence and persisted-exam evaluation
- `lx_dtypes` owns the typed validation contracts
