# Review of implementation direction, October 14, 2026

The examined changes substantially improve error handling and artifact ownership.
They do not yet establish full compliance with all repository standards. This
technical review is a snapshot, not a second approval tracker.
[VideoStorageNormalization.yml](../feature-tracking/VideoStorageNormalization.yml)
and [Reporting.yml](../feature-tracking/Reporting.yml) remain authoritative.

## Scope and evidence limits

The review covered changes confirmed by owner `codex-endoreg-durable-01a1398f`
for report retries, video rebuilds, generation cleanup, and provenance recovery,
together with their tests and the recovery component in lx-annotate. The referenced
chat thread was not directly readable; changes and tracker messages were accessible.
Independent annotation and jumpmark changes in the shared checkout were not
attributed to this agent. Concurrent editing can shift line numbers.

## Findings

### R1 — High: encrypted staging boundary remains unproven

**Rule:** Local media, including temporary raw data, must remain inside the
approved encrypted storage boundary.

**Evidence:** `endoreg_db/services/hub/video_provenance_recovery.py:88` and `:104`
materialize processed and raw video, respectively, through `ensure_local_file`.
`endoreg_db/utils/storage/files.py:181` through `:193` atomically writes decrypted
bytes with mode `0600` under `get_runtime_paths().transcoding`. This path checks
containment and permissions, but not whether storage is actually encrypted.

**Impact:** Without a protective storage layer, raw data can reside on disk in
plaintext. Normal cleanup does not protect against process termination or physical
disk access. This is an existing infrastructure/helper gap also used by the new
recovery path, rather than newly introduced cryptography.

**Correction:** Establish the approved storage boundary in deployment and require
its verification before such operations. Use existing LuxNix support and central
storage helpers. Do not add custom encryption to the recovery service. A test
with an encrypted source blob and subsequent temporary-file deletion does not
prove encryption of the temporary disk. Independently attested hardware or
filesystem encryption could close this deployment gap; current evidence for it
is outside this source review.

### R2 — Medium: repeated recovery is not fully idempotent

**Rule:** Repeating the same logical operation must not create a new domain state
transition.

**Evidence:** `video_provenance_recovery.py:130` through `:143` saves `meta` and
`date_modified` again and creates a new `AuditLedger` entry on every
`dry_run=False`. Even an already identical `previous` receipt is not recognized
as a repeat. Tests cover preview, apply, and rejections, but not an identical
second apply call.

**Impact:** Retrying after a lost HTTP response creates additional confirmations
and repeats expensive media checks. This does not automatically change media
bytes; the finding concerns domain mutation and auditing.

**Correction:** Check equality or a stable operation key under the same lock and
return the existing receipt. If every HTTP attempt must be audited, distinguish
it from the single domain acknowledgment event. Add regression tests for two
identical calls, differing timestamps, and concurrent repeats.

### R3 — Medium: duplicated API contract and late timestamp validation

**Rule:** Validate external input once at the boundary; use shared types and
generated API contracts as the authoritative source.

**Evidence:** `endoreg_db/views/video/provenance_recovery.py:19` defines a local
request model and `:27` a separate DRF serializer. Both initially accept timestamps
as strings. Only the shared `VideoProvenanceRecovery` constructor checks timezone
and order, after hashing, decryption, and timeline probing. The lx-annotate
component `VideoProvenanceRepairPanel.vue:59` also defines `RecoveryResponse`
manually with `mappingStatus: string` and `electricalMatchingEligible: boolean`,
although the shared contract has narrower literal values. Runtime checking of
individual response fields is positive, but does not replace a shared type source.

**Impact:** Invalid timestamps unnecessarily occupy the media operation; manual
contract copies can diverge. The final Pydantic validator prevents invalid
persistence, so this is not a demonstrated integrity bypass.

**Correction:** Derive request-boundary validation from a shared contract,
connect DRF/OpenAPI to it, and generate the frontend type. Do not introduce
another parallel data transfer object (DTO) hierarchy. Invalid timestamps must
be rejected without media input/output; a contract test must connect request,
schema, and consumer.

## Positively reviewed direction

- Feature YAML remains authoritative; experimental release evidence explicitly
  disclaims being the selected deployment route.
- Shared lx-dtypes receipts, hash helpers, runtime paths, and existing atomic file
  functions are used. New orchestration lives in services.
- Removing blanket frame rollbacks respects artifact ownership: a rebuild job
  does not automatically own existing frame caches.
- Report retry no longer calls terminal failure finalization with deletion.
- Receipt reconciliation requires an authenticated current origin and an absent,
  unreferenced replacement; it does not itself delete video files.
- Authentication, center scope, media leases, hash checks, and clinical approvals
  remain intact. Unresolved provenance is not presented as proven frame mapping;
  electrical matching remains blocked.
- No master key transmission, shared-secret substitution for payload encryption,
  or mutual Transport Layer Security (mTLS) bypass was found in the reviewed
  changes. This is not a complete review of every transport path.

## Verification

Independent full `.devenv/state/venv/bin/pyright` run: **0 errors, 19 warnings**,
mostly missing type stubs and unused symbols. This applies to the shared checkout
at review time.

Reported by the implementation owner, not rerun here: recovery 13 tests passed;
report lane 84 passed/6 conditional skips; frontend 47 passed plus TypeScript/ESLint.
Cleanup and rebuild additions were still being edited. A focused pytest process
was already running; no additional concurrent database test run was started.
Running host services do not prove deployment of the latest source changes.

Findings were delivered to the implementation owner through the feature tracker.
The owner accepted R1–R3 and announced follow-up changes for repeat handling and
shared timestamp/response types. Those changes are outside the reviewed snapshot
and have not yet been independently checked. Structure validation: 65 manifests
valid. Tracker validation: 37 feature definitions valid. These checks confirm
schema and references, not clinical approval. This review changes no application
code, production data, or approval status.
