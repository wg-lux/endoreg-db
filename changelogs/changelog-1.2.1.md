# Release notes: 1.2.1

> **Scope:** changes committed after `v1.0.13.0` (11 August 2026) through
> `15a3750a` (9 September 2026). This document describes the implementation in
> this repository. It does not assert that any change has been deployed or that
> production-readiness criteria are verified; those assessments remain in
> `feature-tracking/*.yml`.

## Summary

Version 1.2.1 substantially hardens hub intake and transfer, makes long-running
media and report workflows more explicit and idempotent, and advances the video
storage and Hypertext Transfer Protocol Live Streaming (HLS) implementation.
It also updates report drafting, centralized training-data access, data-type
ownership, migration history, and documentation governance.

## Security and transfer integrity

- Hub transfer payloads now carry and verify content hashes, including the
  processed-media hash used to bind a transfer to its expected artifact.
- Added a fail-closed envelope-encryption receiver for site-to-hub processed
  media. It validates transfer, node, centre, resource, media-role, plaintext
  hash, and size identity before accepting the stream; authenticates the
  ciphertext while reading it; and rejects unavailable, malformed, or replayed
  envelopes unless the replay is an exact recorded match.
- Recipient private-key loading enforces an absolute, regular, non-symlink
  file; restrictive permissions; and, by default, root ownership. The receiver
  uses X25519 key agreement, a derived wrapping key, and authenticated payload
  encryption. No long-lived master key is part of the transfer path.
- Hub registration gained outer-join safeguards so incomplete related records
  cannot cause transfers to be associated with the wrong resource or centre.
- External identifiers are excluded from sensitive-metadata updates where they
  could collide with independently managed identities.
- Hub intake can receive the transfer artifact through the dedicated transfer
  workflow instead of depending on Django REST Framework multipart handling.

## Import, lifecycle, and idempotency

- Import and reaper work now respect Celery- and database-backed locks. The
  workflow records ownership and fencing information so concurrent workers do
  not apply the same transition unsafely.
- Added explicit state-machine services for upload-job, report-import, and
  general lifecycle transitions. These services make transition ownership,
  recovery, and audit behavior visible rather than relying on scattered model
  mutations.
- Import paths consistently attempt to identify an already-known video. This
  preserves transfer identity during retries and avoids creating a separate
  video record when the same media re-enters through another import path.
- Source-file cleanup is now a fenced, fail-closed operation. Before deletion
  it checks processing history, target integrity, source identity, retention
  policy, active processing or media leases, and HLS-generation consistency;
  it records a cleanup receipt and failure code.
- Added database-recovery and PostgreSQL-focused coverage for operation
  identity, leases, import fencing, and reaper behavior.
- Hub import monitoring now exposes joined-dataset identity and allows an
  operator to dismiss individual overview entries. The corresponding schema
  migrations are `0077_upload_job_overview_dismissal` and
  `0078_video_joined_dataset`.

## Video storage and HLS

- HLS materialization now derives and persists a source-content hash, allowing
  regeneration and reconciliation to distinguish an artifact from a changed
  source. Upload-source fallback and source-hash behavior have focused tests.
- HLS generation and materialization paths were tightened around storage-profile
  validation, encrypted file handling, media-integrity reconciliation, and
  overview data.
- The HLS encoding profile now standardizes NVIDIA NVENC hardware encoding where
  the configured profile permits it. Profile-transition coverage documents the
  expected generation behavior.
- Added `adopt_legacy_hls`, with a dedicated adoption service, to register
  eligible legacy HLS artifacts through an explicit workflow instead of treating
  them as an implicit current generation.
- Frame extraction now uses the canonical extraction function. Training-image
  generation can source frames directly from the video timeline, removing a
  prior requirement that frames be persisted before they can be used for
  training.
- Frame export is ordered before segment blackening where the transfer contract
  requires it, preserving the required labeled-frame export path.

## Reporting, metadata, and clinical data workflows

- Reports can be saved as drafts and later resumed. The associated intake-path
  correction restores report intake for affected recent configurations.
- Report and patient-examination data handling was aligned with the deployed
  knowledge-base identity owned by `lx-dtypes`. Module and version are treated
  as a pair and are persisted together when assigned.
- Name-field handling was integrated across anonymization and persistence
  boundaries, with focused updates to sensitive metadata and report processing.
- Added the ColoReg annotation template and related test coverage.
- Centre access is now evaluated centrally by the hub for the affected
  training-data workflow, rather than requiring previously persisted frames.

## Persistence, types, and migrations

- Consolidated the migration history into the canonical graph and updated the
  migration boundary. This removes the split-history path and aligns migration
  tests with the authoritative sequence.
- Clarified persistence-versus-manipulation responsibilities by moving workflow
  behavior toward services and narrowing model-layer responsibilities.
- Updated the `lx-dtypes` compatibility boundary and transferred ownership of
  deployed knowledge-base version identity to that package.
- Corrected centre helper defaults and data-loading root resolution, including
  removal of obsolete duplicated loader material.
- Addressed broad typing issues across models, serializers, services, and
  tests, with expanded type-oriented test coverage.

## Audit, operations, and developer experience

- The Rust-backed policy layer now detects stray jobs and tampering more
  strictly. New ownership ledgers, HLS source-content-hash persistence, and
  lifecycle-state support extend the audit trail and integrity checks.
- Added an audit-ledger operations runbook and updated centre-access, data
  loader, and video-storage operational documentation to describe the changed
  boundaries.
- Feature tracking gained Model Context Protocol integration and updated lock
  coordination support.
- Continuous integration now uses the repository-standard environment, aligning
  workflow configuration with local project tooling.
- Documentation, historical release notes, quality baselines, and generated
  inventories were pruned and aligned with the documentation-governance rules.

## Compatibility and operator notes

- Apply the included migrations before using the new upload-overview dismissal,
  joined-dataset, HLS artifact hash, and operation-ownership fields.
- Envelope-encrypted hub intake requires appropriately provisioned recipient
  private keys and the relevant hub configuration. Invalid or unavailable key
  material is intentionally rejected rather than falling back to unauthenticated
  media intake.
- Legacy HLS artifacts require explicit assessment and adoption; they are not
  silently promoted to the current generation.
- The release includes significant internal refactoring. Integrations should
  continue to use public service and API boundaries rather than importing
  relocated implementation modules.

## Commit range

`v1.0.13.0..15a3750a` contains 36 commits. Notable implementation commits:

- `95108f99` — hub-transfer envelope encryption.
- `88bdf5ed`, `0597c16f`, `5cc169b6` — lock-aware and idempotent import,
  cleanup, and video identity behavior.
- `d0e72e67` — stricter policy/audit handling and lifecycle state machines.
- `80662f24`, `068fab48`, `15a3750a` — HLS source identity, NVENC profile
  standardization, and legacy-HLS adoption.
- `704f56f7`, `74b297df` — report drafting and intake-path corrections.
