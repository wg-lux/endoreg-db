# New Setup: General Purpose

## Purpose
This page explains the general purpose of the new setup so contributors understand:
- why it exists
- what problems it solves
- what should remain configurable
- what is considered stable contract surface

## General Purpose (Plain Language)
The setup exists to provide a repeatable way to run `endoreg_db` with:
- a predictable base dataset
- stable API behavior for frontend clients
- configurable domain/content data through YAML
- support for reporting + media workflows without manual DB editing

## Design Intent

### 1. Repeatability
A new environment (dev/test/staging) should be bootstrapped by:
- configuration
- migrations
- data loader commands

Not by:
- ad-hoc admin UI clicking
- manual SQL changes
- undocumented one-off scripts

### 2. Configurability
Project/domain content changes should prefer:
- YAML files
- loader commands

instead of hardcoded Python changes for every content update.

### 3. Stable API Contracts
Frontend integrations depend on stable contracts for:
- lookup
- report persistence
- media/report artifact access

API/documentation conventions:
- snake_case request/response keys
- REST endpoints (DRF)
- video/report endpoints under `/api/media/...`

### 4. Separation of Concerns
- Application code defines behavior.
- YAML data defines content/reference records.
- Loader commands handle persistence into DB.
- Frontend uses API contracts, not DB assumptions.

## What This Setup Is Not
- It is not a one-time migration note.
- It is not only for local development.
- It is not tied to a single frontend implementation.
- It is not a replacement for formal deployment docs.

## Who This Helps
- Backend developers adding new domain seed data
- Frontend developers integrating reporting flows
- QA/test setup maintainers
- Operators preparing staging/prod bootstrap procedures

## Lifecycle Expectation
- These wiki pages are the canonical in-repo references for setup/workflow behavior.
- Temporary handoff/design docs should be deleted or merged when superseded.

## Practical Rule Summary
- Prefer YAML config.
- Reference and use `load_base_db_data` for base bootstrap flows.
- Keep contracts snake_case.
- Keep heavy report/video interactions under `/api/media/...`.
