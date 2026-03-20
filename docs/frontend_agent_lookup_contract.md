# Frontend Agent Lookup Contract

This contract is retired.

The frontend should no longer call or depend on `/api/lookup/*`.

## Replacement flow

1. Persist transient reporting state through `GET|PUT /api/patient-examinations/{pk}/draft/`
2. Use the typed validation/report endpoints for backend validation
3. Finalize into relational persistence through the report save/finalization flow

## Current boundary

- frontend owns draft/editor state
- `endoreg_db` stores draft blobs and finalized relational data
- `lx_dtypes` validates typed knowledge-base and ledger payloads
