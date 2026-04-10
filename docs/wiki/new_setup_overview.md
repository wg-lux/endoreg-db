# New Setup Overview

## Purpose
This page explains the new setup at a high level:
- what gets installed/configured
- what data must be loaded
- which parts are runtime dependencies vs seed data
- how frontend/backend contracts fit together

## Scope
The new setup covers:
- backend application runtime (`endoreg_db`)
- base database seed data
- report/lookup workflow support
- media/report handling under `/api/media/...`
- optional external tools (for example report PDF renderer)

## Core Components
- `endoreg_db` Django backend (REST API)
- seeded reference data (loaded via management commands)
- YAML-based source files for dataloader-managed entities
- frontend clients consuming API contracts (snake_case)
- media/report endpoints (streaming/report artifacts)

## Setup Layers

### 1. Application Runtime
Install Python dependencies and configure environment for Django runtime.

Typical concerns:
- database connection
- storage/media paths
- auth configuration
- proxy deployment (`/api/` prefix added by proxy/project routing)

### 2. Base Data Bootstrap
Load required reference data before normal use.

Preferred bootstrap reference:
- `endoreg_db.helpers.data_load_orchestrator.load_base_db_data`

Operationally this maps to the Django management command:
- `load_base_db_data`

This should be part of:
- developer setup
- test setup (where relevant)
- staging/prod initialization (with review)

### 3. Domain Data Extensions (YAML)
Additional or custom domain entries should be authored in YAML and loaded through the existing dataloader commands.

Examples:
- findings
- requirements
- tags
- medications
- units
- organ/examination-related metadata

### 4. Reporting Workflow Integration
The new reporting flow depends on:
- lookup session endpoints (`/api/lookup/...`)
- report persistence (`/api/patient-examination-reports/save-submission/`)
- history context (`/api/patient-examination-reports/history-context/`)
- media artifacts (`/api/media/pdfs/...`, `/api/media/patients/.../timeline/`)

## Setup Order (Recommended)
1. Configure backend runtime and database.
2. Run migrations.
3. Load base DB data (`load_base_db_data`).
4. Load/validate custom YAML domain data (if any).
5. Start backend and verify health/API access.
6. Validate reporting flow (lookup init, recompute, draft save, final save).

## Validation Checklist
- Base data loads without dataloader errors.
- Required reference models exist (gender, examination, findings, etc.).
- Lookup init works for a created `patient_examination`.
- Report save returns `warnings`/`requirement_guidance` (advisory, not blocking).
- PDF/media artifact endpoints resolve after final report save.

## Documentation Boundaries
- Use this page for architecture/setup flow.
- Use `docs/wiki/new_setup_general_purpose.md` for why the setup exists and operational intent.
- Use `docs/wiki/dataloader_yaml_authoring.md` for YAML authoring rules and examples.
