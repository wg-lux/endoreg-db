# Persistence ownership

Scope and verification evidence are authoritative in
[`CodeQuality.yml`](../feature-tracking/CodeQuality.yml), criterion
`persistence_ownership`. The consuming application's corresponding criterion is
in `lx-annotate/feature-tracking/StorageOptimization.yml`.

Endoreg-db owns the Django definitions and canonical tables for
`AIDatasetSplitPlan`, `OutboundHubTransferJob`, `StorageArtifactPublication`,
`StorageNodeActionReceipt`, `StorageOperatorDispatchReceipt`, and
`VideoTranscodeJob`. These records describe datasets, media, transfers and storage
operations already owned by Endoreg. None requires application-owned persistence.
`StudyCohort` has its preceding, separate ownership migration.

LX-Annotate retains HTTP handlers, Celery dispatch, host integration and workflow
services. Its former model modules re-export the exact canonical classes for
existing Python consumers. They do not declare proxy or duplicate models.
`endoreg_db.schemas.ai_dataset_splits` owns the strict, immutable snapshot of
local annotation row identities; the former host schema module re-exports it.
Outbound envelope receipts continue to use the shared `lx_dtypes` validator.
Inbound `TransferJob` and outbound `OutboundHubTransferJob` have different
protocol responsibilities and remain distinct ledgers.

## Coordinated upgrade and rollback

Ship matching Endoreg and LX-Annotate packages together. Stop web processes,
watchers and every worker that can write these tables before migrating; old and
new runtimes must not write concurrently. Back up the database under the existing
encrypted backup policy. A dependency package with these models must be released
and pinned before deploying the consuming application; source-checkout tests
alone do not provide release compatibility.

Apply Endoreg migration `0097_application_persistence` and LX-Annotate migration
`0016_application_persistence`, after the preceding study/cohort migrations.
Endoreg's migration graph has no dependency on LX-Annotate and also supports a
standalone installation. New constraints have distinct names so PostgreSQL can
hold both source and destination tables during the cutover.

The host migration checks every target table is empty and every concrete column
has the same shape, copies rows without invoking current model validation,
preserves primary keys and timestamps, checks row counts, transfers content types
and permission assignments, resets generated-key sequences, and removes the empty
legacy tables. PostgreSQL locks all source and target tables for the transaction
and checks deferred foreign keys before table removal. Permission migration also
covers `StudyCohort`; existing target permissions merge user/group assignments,
and administrative history follows the new content type.

A populated target, constraint violation or incompatible column shape aborts
the transaction. Reconcile the conflict explicitly before retrying. Legacy
snapshots are copied verbatim; invalid stored payloads do not become silently
rewritten records. Runtime writes retain their existing validation rules.

For rollback, stop writers again, unapply host migration `0016` first, then
restore the matching previous runtime packages. Reverse migration recreates
legacy tables and moves all rows and permissions back, rejecting occupied legacy
targets. Only unapply Endoreg `0097` after that transfer; unapplying it first
would remove canonical tables containing the transferred records. Keep the
preceding study/cohort migrations applied unless rolling back their separate
workflow too. Resume workers only after checking schema and package compatibility.
