# LXDM host integration and persistence contract

The former lookup-session API has been removed. This document defines the
current integration boundary between `endoreg_db`, `lx_dtypes`, the knowledge
base and the frontend.

## Runtime data flow

1. The frontend sends finding mutations or a report draft scoped by a
   `PatientExamination` URL. It never supplies authorization, center ownership,
   audit actors or knowledge-base definitions.
2. The lx-dtypes Django routes validate clinical concept names and relations
   against the selected knowledge-base module/version.
3. `endoreg_db.integrations.lx_dtypes_host_models` resolves host ORM models,
   authenticates the request and enforces center-scoped object access.
4. The host ledger rows are converted to the canonical
   `DtypesRecordPersistencePayload` by lx-dtypes.
5. `endoreg_db.services.dtypes_records` verifies the URL/model examination and
   each nested finding's examination reference, then writes the canonical JSON
   and knowledge-base identity in a transaction.

The active entrypoints are:

- `GET|PUT /api/patient-examinations/{pk}/draft/` for frontend-owned draft state;
- `POST /api/evaluate-requirements/` for advisory evaluation of a persisted
  examination;
- `/dtypes-api/patient-findings/*` for typed finding mutations;
- lx-dtypes ledger/report validation routes for explicit typed payloads.

## Field ownership

| Data | Authoritative owner | Persisted by |
| --- | --- | --- |
| patient, examination and center relations | endoreg-db ORM | endoreg-db |
| authentication principal and delete actor/time | endoreg-db auth/audit policy | endoreg-db |
| finding/classification/intervention ledger rows | endoreg-db ORM after lx-dtypes route validation | endoreg-db |
| clinical concept definitions and valid relations | versioned knowledge-base module | lx-dtypes loader |
| LXDM JSON shape and nested field types | `lx_dtypes.models.contracts.DtypesRecordPersistencePayload` | endoreg-db JSONField |
| knowledge-base module/version in the record | host deployment plus loaded KB identity | endoreg-db |
| transient report editor state and patient-facing titles | frontend/report draft | not part of the LXDM record contract |

Host database IDs, center IDs, request users and audit timestamps are never
trusted from an LXDM payload. The URL-scoped host object and authenticated
principal remain authoritative.

## Canonical imports and compatibility

Host code imports the persistence type and parser from
`lx_dtypes.models.contracts.dtypes_record_persistence` (also exported by
`lx_dtypes.models.contracts` from 0.2.1):

```python
from lx_dtypes.models.contracts import (
    DtypesRecordPersistencePayload,
    parse_dtypes_record_persistence_payload,
)
```

There is one persisted schema: the complete strict `PExamination` graph exposed
as `DtypesRecordPersistencePayload`. Unknown root or nested fields are rejected.
Endoreg must not introduce a reduced serializer or retain unvalidated dicts
inside the workflow.

The strict contract and authenticated host callbacks require
`lx-dtypes>=0.2.1,<0.3`. Endoreg must not deploy its associated route changes
while pinned to 0.2.0. Patch releases are schema-compatible. A required field,
field meaning change or incompatible nesting requires a new lx-dtypes minor
version and an Endoreg adapter/backfill rehearsal before rollout.

## Consistency and security invariants

- The payload examination name must match the host `PatientExamination`.
- Every nested finding must reference that same host examination ID.
- Create, update, classification and delete flows rebuild the record from active
  ledger rows, so retries produce the same clinical result.
- Patient-finding lists are authenticated and filtered by the host center scope.
- Create, patch, classification and delete operations authenticate first and
  enforce examination/finding center ownership; foreign-center objects return
  404.
- Deactivation is an audited soft delete with an authenticated actor and
  server-side timestamp.
- Soft delete and record refresh share one database transaction. If record
  validation or persistence fails, the delete is rolled back.
- `PatientExamination.clean()` validates direct JSONField writes through the
  same canonical lx-dtypes parser.

## Upgrade, backfill and rollback

Before upgrading lx-dtypes:

1. Build and test the candidate package, then run Pyright and
   `tests/app/test_lx_dtypes_import_boundary.py` plus
   `tests/api/test_dtypes_record_persistence.py` against it.
2. Take a database backup and record the installed lx-dtypes and knowledge-base
   module/version. Do not combine the package rollout with a KB version change.
3. In a staging database, rebuild representative examinations with
   `persist_patient_examination_dtypes_record_from_ledger`, including empty,
   classified, intervention-bearing and soft-deleted findings. Compare counts
   and validate every resulting JSON value with
   `parse_dtypes_record_persistence_payload`.
4. Deploy the package and Endoreg adapter together. Rebuild remaining records in
   bounded batches; a failure stops that record's transaction and must be logged
   with its examination ID, without writing a partial JSON document.
5. Re-run the same validation and mutation tests after deployment.

If validation or mutation checks fail, stop the backfill, roll Endoreg and
lx-dtypes back as a pair, restore affected JSON rows/database from the preflight
backup, and keep the previous KB identity active. Re-run the old-version
contract parser before reopening writes. The atomic rollback behavior is covered
by `test_patient_finding_delete_rolls_back_when_dtypes_refresh_fails`; a real
backup/restore rehearsal remains a release-environment gate.

## Verification

From `/home/admin/endoreg-db`:

```bash
.devenv/state/venv/bin/pyright
devenv shell -- env PYTHONPATH=/home/admin/lx-data-models \
  .devenv/state/venv/bin/pytest \
  tests/app/test_lx_dtypes_import_boundary.py \
  tests/api/test_dtypes_record_persistence.py -q
devenv shell -- python feature-tracking/tracker.py validate
devenv shell -- python feature-tracking/tracker.py show lxdm
devenv shell -- python feature-tracking/tracker.py check lxdm
```

The `PYTHONPATH` override is only for validating an unpublished sibling checkout.
Production must install the released, pinned wheel and run without source-path
injection.
