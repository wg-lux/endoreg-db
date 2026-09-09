# Audit Ledger: Contract and Operations

This document describes the binding application and operational contract
of the persisted `AuditLedger` in `endoreg_db`. Implementation and
approval status is maintained exclusively in
[`feature-tracking/AuditLedger.yml`](../feature-tracking/AuditLedger.yml).

## Purpose and Trust Boundary

The ledger demonstrates that selected security- and approval-relevant
actions were persisted in a particular order and were not subsequently
changed without detection. Each entry contains the hash of its predecessor.
The `LedgerHead` singleton is locked during append within the same database
transaction and set to the new entry.

The ledger detects tampering, but it is neither a digital signature nor an
external timestamping service. Anyone who controls both ledger rows and the
ledger head, as well as all backups, is outside the demonstrated trust model.
The database, its permissions, backups, and the encrypted storage area must
therefore be protected independently.

The integrity status is global for the local ledger chain. It does not return
ledger rows and is not a center-filtered event search. The
production-authenticated status endpoint
`GET /endoreg-api/audit-ledger/integrity/` returns only the result of the
most recent background check. A full chain check in the web process is
excluded.

Structured hub events under the logger `endoreg_db.hub.audit` are a
separate operational log channel. They do not automatically become
`AuditLedger` rows and do not have its hash-chain proof.

## Persisted Event Format

A ledger row consists of:

- immutable UUID and server-side timestamp;
- optional authenticated Django user;
- `object_type`, `object_pk`, and stable `action` code;
- a canonical JSON object `data`;
- `prev_hash` and `hash` as SHA-256 hexadecimal values.

The hash payload is defined by
`lx_dtypes.models.contracts.audit_ledger.AuditLedgerHashPayload`.
`data` accepts only finite, JSON-compatible values with string keys.
Existing rows must not be updated through the model path.

## Actions Requiring Auditing

The currently intentional persisted producers are:

| Action | Business rationale | Required evidence | Error behavior |
| --- | --- | --- | --- |
| `identity_committed` | Binding validated media metadata to pseudonymous case identities | Media type and ID, SensitiveMeta-ID, hash identities, pseudonymous and linked IDs, resolution result, and payload hash | Identity resolution remains transactional; the generic append helper cannot create an entry when the ledger table has not yet been migrated and logs this |
| `ready_for_export` | Approval of a validated, redacted processed video artifact | Center key, persisted relative artifact name, SHA-256, and approval status; no absolute path | A ledger entry that cannot be proven persisted aborts export approval with HTTP 503 and rolls back the state change |
| `center_admin_bootstrapped` | Promotion of an already Keycloak-synchronized member from `center_scope:admin` | Target user, required group, previous and new roles, and initiator | A missing ledger proof aborts the promotion and rolls back the transaction |
| `media_storage_migrated` / `media_storage_failed` | Idempotent media storage migration or its classified failure | Object reference and migration evidence from the management command | Unavailable ledger tables are surfaced as a warning; the migration command manages its own failure decision |

New actions requiring auditing need a stable action code, a named producer, a
minimal typed payload, a clear transaction boundary, and negative tests for
ledger failure before their introduction. A general request, ORM, or debug
capture does not belong in the ledger.

## Data Minimization and Known Limit

Raw media, file content, Large-Language-Model prompts, complete request
payloads, plaintext secrets, the master key, passwords, tokens, private keys,
stack traces, and direct patient identifiers not required for the proof are
intentionally excluded. Pseudonymous IDs and hash values may be included only
when necessary for the specific correlation. The ledger must never be used as
a substitute for primary clinical data.

The `ready_for_export` event persists only the relative managed storage name
and content hash, not the absolute resolved path. New events must likewise not
include absolute paths. The global integrity endpoint returns neither this
payload nor individual ledger rows, requires authentication in production
mode, and provides only a `GET` operation.

## Integrity States and Check Frequency

The background check distinguishes the following states:

| Status | Meaning | Operational decision |
| --- | --- | --- |
| `verified` | Every row, every predecessor link, and the ledger head match | Regular operation may continue |
| `failed` | The chain or head does not match the persisted hash values | Fail-closed incident; no repair or overwrite |
| `error` | The check could not be completed because of a runtime or infrastructure error | Resolve the cause and check again; until then, do not treat it as verified |
| `unknown` | No valid cached check result is available | Do not treat it as verified; perform a background or operator check |

By default, Celery Beat schedules the task
`endoreg_db.refresh_audit_ledger_integrity_status` every 300 seconds on the
separate maintenance queue. The value is configurable through
`CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS`, but must be at least 60
seconds. Scheduling is enabled by default and can be disabled through
`CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_ENABLED`; a production profile then
requires an equivalent documented external invocation.

A cache lock with a 30-minute lifetime prevents parallel full checks. If the
lock is already held, the last status remains visible and receives the source
`skipped_locked`. Missing cache content, or cache content that cannot be read
as an object, results in `unknown` and `verified=false` fail-closed.

## Operation and Alerting

An explicit lock-aware check is run in the installed runtime environment:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  refresh_audit_ledger_integrity --once --fail-on-non-verified
```

Machine-readable output is JSON. `--pretty` formats it for manual
diagnostics. `--fail-on-non-verified` returns an error code for every status
other than `verified` and is required for deployment and recovery gates.

The general health check then checks the cached state:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  check_system_health --json
```

In the `local_study_server` profile, an unverified ledger causes the health
check to fail. `audit_ledger.integrity_failed` and
`audit_ledger.integrity_error` are emitted as structured error events with
head hash, last entry ID, and—where available—entry count. Error output must
not contain ledger payloads or patient data.

## Incident Procedure

For `failed`, `error`, or an unexpectedly old `unknown`, the following applies:

1. Stop write-capable approval, admin, and migration workflows; do not
   manually set the state to `verified`.
2. Capture the database snapshot, ledger tables, `LedgerHead`, relevant
   structured logs, task ID, host time, and deployment version for evidentiary
   preservation.
3. Run the lock-aware management command exactly once more and compare the
   JSON, exit code, and ledger head. Do not start parallel full scans.
4. For `error`, first resolve the cache, database, migration, or worker cause.
   For `failed`, assume tampering or corruption and involve Security and
   Operations.
5. Do not automatically repair, rehash, truncate, or merge the database and
   ledger from a partially matching source. Preserve raw rows and previous
   backups unchanged.
6. Restore only from a matching, verified database backup. Then check the
   complete chain and reopen write-capable workflows only after documented
   approval.

## Retention and Change Control

There is currently no automatic retention or deletion path for
`AuditLedger` rows. They are backed up together with the database. A future
retention or archival rule requires a versioned chain/checkpoint concept,
Security and Operations approval, and a restoration test; ordinary data
cleanup must not delete ledger rows.

Changes to the hash format, action codes, or existing payload fields are
persistence contract changes. They require an lx-dtypes compatibility
strategy, migration tests, forward/rollback evidence, and an update to this
document and the feature evidence.

## Verification

From `/home/admin/endoreg-db`:

```sh
.devenv/state/venv/bin/pytest \
  tests/models/state/test_audit_ledger.py \
  tests/services/test_audit_integrity.py \
  tests/services/test_hub_audit.py \
  tests/management/commands/test_refresh_audit_ledger_integrity.py \
  tests/views/misc/test_stats_endpoints.py \
  tests/management/commands/test_check_system_health.py -q
./feature-tracking/tracker.py validate
./feature-tracking/tracker.py show audit_ledger
./feature-tracking/tracker.py check audit_ledger
```
