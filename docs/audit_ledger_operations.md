# Audit ledger operations

Use this runbook to check integrity and respond to ledger incidents.
[AuditLedger.yml](../feature-tracking/AuditLedger.yml) owns readiness evidence.

## Operation and Alerting

Run a lock-aware check in the installed runtime:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  refresh_audit_ledger_integrity --once --fail-on-non-verified
```

Output is JSON; add `--pretty` for readability. `--fail-on-non-verified` exits
non-zero unless the result is `verified`. Use it for deployment and recovery gates.
Then inspect cached system health:

```sh
/opt/endoreg-db/venv/bin/python /opt/endoreg-db/manage.py \
  check_system_health --json
```

An unverified ledger fails health checks on `local_study_server`.
`audit_ledger.integrity_failed` and `audit_ledger.integrity_error` log the head
hash, last entry ID and, when available, entry count. Never log ledger payloads
or patient data.

## Integrity States and Check Frequency

| Status | Operator action |
| --- | --- |
| `verified` | All rows, predecessor links and the head match; continue operation. |
| `failed` | Treat as a tampering or corruption incident; do not repair or overwrite. |
| `error` | Resolve the runtime or infrastructure cause and check again. |
| `unknown` | No valid cached result exists; run a background or operator check. |

Celery Beat runs `endoreg_db.refresh_audit_ledger_integrity_status` on the
maintenance queue every 300 seconds. Configure the interval with
`CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_INTERVAL_SECONDS` (minimum 60 seconds).
Disabling `CELERY_BEAT_AUDIT_LEDGER_INTEGRITY_ENABLED` in production requires
a documented equivalent external invocation.

A 30-minute cache lock prevents parallel full scans. A competing check retains
the previous status with source `skipped_locked`. Missing or unreadable cache
objects yield `unknown` and `verified=false`.

## Incident Procedure

For `failed`, `error`, or unexpectedly old `unknown`:

1. Stop write-capable approval, administration and migration workflows.
2. Preserve the database snapshot, ledger rows, `LedgerHead`, structured logs,
   task ID, host time and deployment version.
3. Run the lock-aware check once more; compare JSON, exit code and head.
   Do not start parallel scans or manually mark the ledger verified.
4. For `error`, resolve cache, database, migration or worker faults.
   For `failed`, involve Security and Operations.
5. Preserve raw rows and backups. Never rehash, truncate, auto-repair or merge
   partially matching sources.
6. Restore a matching verified database backup, check the complete chain, and
   obtain documented approval before reopening writes.

## Purpose and Trust Boundary

Each `AuditLedger` entry hashes its predecessor. Appending locks the singleton
`LedgerHead` in the same database transaction. Verification walks backwards from
that head, checks each row once and validates the last-entry pointer. It does
not sort by timestamp or UUID. The head remains locked during the scan, blocking
appends; memory use grows with ledger size. Existing hashes are not rewritten.

This detects tampering but provides neither digital signatures nor external
timestamps. Control of the rows, head and all backups defeats this trust model;
protect the database, permissions, backups and encrypted storage separately.

Production-authenticated `GET /endoreg-api/audit-ledger/integrity/` returns only
the latest cached global result, never ledger rows or a center-filtered search.
Web requests do not run full scans. The `endoreg_db.hub.audit` log channel is
separate and does not acquire ledger hash-chain proof.

## Persisted Event Format

Rows contain an immutable UUID, server timestamp, optional authenticated Django
user, `object_type`, `object_pk`, stable `action`, canonical JSON `data`, and
SHA-256 `prev_hash`/`hash`. The hash contract is
`lx_dtypes.models.contracts.audit_ledger.AuditLedgerHashPayload`.
Data keys must be strings and values finite and JSON-compatible. Model updates
to existing rows are prohibited.

## Actions Requiring Auditing

| Action | Business rationale | Required evidence | Error behavior |
| --- | --- | --- | --- |
| `identity_committed` | Binding validated media metadata to pseudonymous case identities | Media type and ID, SensitiveMeta-ID, hash identities, pseudonymous and linked IDs, resolution result, and payload hash | Any audit persistence failure propagates and rolls back transactional identity resolution; missing migrations must be fixed before retry |
| `ready_for_export` | Approval of a validated, redacted processed video artifact | Center key, persisted relative artifact name, SHA-256, and approval status; no absolute path | A ledger entry that cannot be proven persisted aborts export approval with HTTP 503 and rolls back the state change |
| `center_admin_bootstrapped` | Promotion of an already Keycloak-synchronized member from `center_scope:admin` | Target user, required group, previous and new roles, and initiator | A missing ledger proof aborts the promotion and rolls back the transaction |
| `media_storage_migrated` / `media_storage_failed` | Idempotent media storage migration or its classified failure | Object reference and migration evidence from the management command | Audit writes propagate database errors; the migration command manages its own failure decision |


New actions require a named producer, stable code, minimal typed payload,
transaction boundary and ledger-failure tests. Request, ORM and debug captures
do not belong in the ledger.

## Data Minimization and Known Limit

Exclude raw media, file contents, model prompts, full requests, secrets, keys,
passwords, tokens, stack traces and unnecessary direct patient identifiers.
Use pseudonymous IDs and hashes only when the proof requires them; the ledger
is not clinical data storage. `ready_for_export` stores a relative managed
name and content hash. New events must also exclude absolute paths.

## Retention and Change Control

Back up ledger rows with the database; there is no automatic deletion or
retention path. Archival requires versioned chain/checkpoint design, Security
and Operations approval, and a restore test. Ordinary cleanup must preserve rows.

Hash formats, action codes and payload fields are persistence contracts.
Changes require lx-dtypes compatibility, migration and rollback tests, and
updated documentation and feature evidence.

## Verification

From the repository root:

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
