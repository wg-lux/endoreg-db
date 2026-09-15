# LXDM host integration and persistence contract

The former lookup-session API has been removed. This document defines the
current integration boundary between `endoreg_db`, `lx_dtypes`, the knowledge
base and the frontend.

## Runtime data flow

1. The frontend obtains the available knowledge-base module/version pairs from
   the lx-dtypes discovery endpoints and owns the user's current selection. It
   does not reconstruct an active-version or default-selection policy.
2. Report save and export requests submit both `knowledge_base_module` and
   `knowledge_base_version` from that selected bundle. Finding mutations remain
   scoped by a `PatientExamination` URL. The frontend never supplies
   authorization, center ownership, audit actors or knowledge-base definitions.
3. Endoreg validates the exact submitted identity through lx-dtypes. A missing
   half, unknown pair or resolver mismatch fails visibly and is never replaced
   by an active or default identity.
4. The lx-dtypes Django routes validate clinical concept names and relations
   against the selected knowledge-base module/version.
5. `endoreg_db.integrations.lx_dtypes_host_models` resolves host ORM models,
   authenticates the request and enforces center-scoped object access.
6. The host ledger rows are converted to the canonical
   `DtypesRecordPersistencePayload` by lx-dtypes.
7. `endoreg_db.services.dtypes_records` verifies the URL/model examination and
   each nested finding's examination reference, then writes the canonical JSON
   and knowledge-base identity in a transaction.

The active entrypoints are:

- `GET|PUT /api/patient-examinations/{pk}/draft/` for frontend-owned draft state;
- `POST /api/patient-examination-reports/save-submission` for transactional
  report persistence with an explicit knowledge-base identity;
- `POST /api/patient-examination-reports/make-report` for export using the same
  identity already bound to the examination and report;
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
| knowledge-base identity semantics and resolvable module/version pairs | lx-dtypes contracts and registry | not persisted by discovery |
| current knowledge-base selection | lx-annotate frontend user state | submitted explicitly to endoreg-db |
| examination and report knowledge-base module/version | explicit frontend selection after lx-dtypes validation | endoreg-db |
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

## Explicit selection and persistence

`knowledge_base_module` and `knowledge_base_version` form one indivisible
identity. The frontend must copy both values from one backend-provided bundle
into every report save or export request. A request must not infer a version
from a module name or reuse a nominal deployment default.

At the Endoreg boundary, the shared lx-dtypes request contract rejects missing
fields. The persistence service constructs `KnowledgeBaseIdentity`, resolves
that exact module/version pair, locks the target `PatientExamination`, and binds
the pair before ledger persistence and final report-template validation. The
same transaction copies the identity to `PatientExaminationReport`; a failed
validation rolls the binding and report write back together.

The save response returns the persisted report identity. Subsequent examination
loads reconstruct the same pair from the database rather than consulting the
currently active registry entry. Export requires the submitted pair to match
both the persisted `PatientExamination` and report identities; disagreement is
a conflict, not a migration or fallback signal.

Changing a bound identity is therefore an explicit, controlled migration: the
frontend submits another resolvable complete pair to the save endpoint, and the
transaction updates the examination and report together. Direct model writes
also enforce the all-or-none pair invariant, but callers should use the service
boundary so resolution, locking and report synchronization cannot be skipped.

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
2. Take a database backup and inventory the installed lx-dtypes version plus
   every distinct persisted knowledge-base module/version pair. Do not combine
   the package rollout with a knowledge-base version migration.
3. In a staging database, rebuild representative examinations with
   `persist_patient_examination_dtypes_record_from_ledger`, including empty,
   classified, intervention-bearing and soft-deleted findings. Compare counts
   and validate every resulting JSON value with
   `parse_dtypes_record_persistence_payload`.
4. Confirm that the candidate registry resolves every persisted pair exactly.
   Missing or incompatible identities block the rollout; they must not be
   replaced with the registry's active or default pair.
5. Deploy the package and Endoreg adapter together. Rebuild remaining records in
   bounded batches while retaining each examination's persisted identity; a
   failure stops that record's transaction and must be logged with its
   examination ID, without writing a partial JSON document.
6. Re-run the same validation and mutation tests after deployment.

If validation or mutation checks fail, stop the backfill, roll Endoreg and
lx-dtypes back as a pair, restore affected JSON rows/database from the preflight
backup, and restore a registry that resolves every previously persisted
identity. Do not rewrite persisted identities to whichever pair is active.
Re-run the old-version contract parser before reopening writes. The atomic
rollback behavior is covered by
`test_patient_finding_delete_rolls_back_when_dtypes_refresh_fails`; a real
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
