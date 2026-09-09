# Implementation Guide for Stable Concurrent Report Imports

This guide defines the target architecture for PDF report imports in
`endoreg-db` and the call boundary to `lx-anonymizer`. It is a technical
contract, not a progress checklist. Scope, acceptance criteria, and production
approval are tracked only in
[`feature-tracking/Reporting.yml`](../feature-tracking/Reporting.yml).

The corresponding library-side contract lives in the `lx-anonymizer`
repository at `docs/report_import_concurrency_contract.md`.

## Goal and scope

`ReportImportService.import_and_anonymize(...)` must remain deterministic when
workers overlap, a source changes, a job is retried, or identical content is
uploaded more than once. It may publish at most one canonical anonymized report
and must never mark a partially written artifact as successful.

Native Rust functions should perform large local file operations outside the
Python Global Interpreter Lock. Rust does not own domain state transitions,
database transactions, or publication policy.

This guide covers source claiming, sensitive working snapshots, content-based
deduplication, retries, the typed `lx-anonymizer` boundary, PDF validation and
publication, cleanup, reconciliation, observability, and native-module
packaging. It does not change clinical detection or redaction logic, permit raw
report export, weaken encrypted storage, use `NetworkNode.shared_secret` for
payload encryption, or create another status tracker.

## Required invariants

1. Raw input and sensitive working snapshots remain inside the approved
   encrypted storage boundary.
2. `endoreg-db` owns source intake, claiming, snapshots, locking, persistence,
   deduplication, canonical publication, and cleanup.
3. `lx-anonymizer` owns only content extraction and anonymization.
4. Database success is written only after PDF validation, hash verification,
   and atomic publication.
5. Every attempt writes only to its unique attempt directory. Global temporary
   paths and paths derived only from the content hash are prohibited.
6. A stale, expired, or superseded worker cannot overwrite a newer attempt.
7. Invalid or inconsistent state fails loudly; there is no silent repair or
   silent downgrade to a weaker security profile.
8. Filesystem mutations initiated by `endoreg-db` use the typed wrappers in
   `endoreg_db.utils.filesystem.file_operations` and emit structured JSON logs.

## Target workflow

1. A producer writes under an unobserved temporary name, synchronizes the file
   and directory, and atomically renames it to the observed source name.
2. The importer validates the path, regular-file type, storage boundary, and
   extension without trusting the filename as domain evidence.
3. The worker claims the source path.
4. A typed filesystem wrapper invokes the native snapshot function, which
   copies and hashes the same open file in one pass and atomically publishes the
   snapshot inside a unique attempt directory.
5. The returned SHA-256 hash is used to acquire the content claim or database
   fence.
6. A short transaction idempotently creates an attempt or finds an already
   usable result.
7. `lx-anonymizer` processes only the immutable sensitive snapshot and writes
   only inside the attempt directory.
8. `endoreg-db` validates the result format, size, hash, provenance, and
   readability.
9. The result is atomically published at the canonical target.
10. Success is committed only with the current fencing token.
11. Non-canonical attempt artifacts and the import source are removed according
    to lifecycle policy.

The lock order is always:

```text
source-path claim
  -> stable snapshot
    -> content claim
      -> short database transaction
        -> anonymization attempt
          -> validated atomic publication
```

A later stage must never acquire an earlier lock in reverse order.

## Native snapshot contract

The Rust implementation belongs in `rust/endoreg_rust_backend`. Python calls it
through a typed filesystem wrapper; import services do not call the PyO3 module
directly for mutations.

A versioned result type contains at least:

```python
@dataclass(frozen=True)
class ReportSourceSnapshot:
    contract_version: Literal["report_source_snapshot_v1"]
    staging_path: Path
    size_bytes: int
    modified_time_ns: int
    sha256: str
```

The native operation, conceptually
`stable_snapshot_to_path(source, temporary_target, chunk_size)`, must open the
source exactly once; reject symbolic links and non-regular files; compare
device, inode, size, and nanosecond modification time before and after reading;
verify that the path still identifies the same file; copy and calculate SHA-256
in one pass while releasing the Python Global Interpreter Lock; reject short
writes and premature end-of-file; synchronize file and directory; publish only
by atomic rename within one filesystem; and clean up unpublished temporary
targets on error. It never mutates database or canonical application state.

The Python boundary maps Rust failures to concrete typed errors. Broad
`RuntimeError` handling or string inspection is not the durable contract.

## Locks, leases, and fencing

An age-only lock file is insufficient for long-running clinical jobs. The
target design separates a local operating-system-managed advisory lock such as
`flock`, cluster-wide content coordination through database state or a lease,
and fencing through a monotonic attempt token.

A lease contains an owner ID, host ID, attempt token, creation time, expiry, and
heartbeat. It contains no personal data or raw paths. Finalization atomically
checks current ownership, fencing-token equality, complete validation of the
canonical target, and absence of a newer attempt.

A uniqueness constraint on canonical content identity remains the last defense
against duplicate rows. `IntegrityError` handling may catch only the expected
uniqueness conflict and must then fully validate the winning result.

## `lx-anonymizer` contract

The long-term library call accepts a typed `ReportAnonymizationRequest` with a
contract version, immutable snapshot path, expected source hash and size,
unique attempt ID, attempt-owned output directory, explicit feature/provider
options, and cancellation or deadline information.

A versioned `ReportAnonymizationResult` returns the original and anonymized
text, validated sensitive metadata, unpublished output path, output size and
SHA-256, contract/package/model/rule versions, and deterministic warnings and
quality information. Shared types should live in `lx_dtypes`; the repositories
must not maintain divergent untyped dictionaries as parallel contracts.

`endoreg-db` never accepts a canonical target chosen by `lx-anonymizer`. The
library writes only to the supplied attempt directory, and the import service
owns canonical publication.

## Python fallback and production profile

The Python fallback must be explicit, enforce the same postconditions as the
native implementation, emit a structured event and metric, and produce the same
hashes and error classes in parity tests.

In the target production profile, `report_source_snapshot_v1` is a required
native capability and its absence fails readiness. A development profile may
use the fallback but cannot claim to have tested native concurrency. The native
module exposes a machine-readable capability function, for example:

```text
native_capabilities()
  -> [("report_source_snapshot_v1", contract_version, implementation_version)]
```

This remains target behavior until the corresponding criterion in
`Reporting.yml` has verified evidence; this guide is not deployment evidence.

## Failure and cleanup matrix

| Failure point | Required behavior |
| --- | --- |
| Source changes during snapshot | Abort before database mutation and remove the temporary snapshot |
| Process dies during snapshot | Publish no target; reconciliation removes the orphaned temporary artifact |
| Identical content is imported concurrently | One winner; losers validate and reuse the canonical result |
| `lx-anonymizer` fails | Record a failed attempt and publish no canonical result |
| Result PDF is invalid | Quarantine or clean the attempt according to policy; never mark success |
| Database fails after file publication | Detect an unreferenced artifact; reconciliation attaches it or quarantines fail-closed |
| Worker loses its lease | Reject every subsequent state mutation and publication |
| Native capability is missing | Reject production startup; only an explicit development profile may fall back |

Cleanup operates only below a resolved approved root and never removes the only
validated canonical result.

## Concurrency, observability, and verification

File copies and hashing run natively without the Python Global Interpreter
Lock. Database transactions remain short and exclude optical character
recognition, language models, and full-file copies. Per-host anonymization and
nested native or machine-learning thread pools have explicit limits. Queue
backpressure is preferred to an unbounded local executor. Cancellation
terminates and joins subprocesses before releasing the lease or attempt
directory.

Required structured events include `report_import.source_claimed`,
`report_import.snapshot_started`, `report_import.snapshot_completed`,
`report_import.snapshot_rejected`, `report_import.content_claim_waited`,
`report_import.duplicate_reused`, `report_import.anonymizer_started`,
`report_import.anonymizer_completed`, `report_import.publication_completed`,
`report_import.fencing_rejected`, `report_import.cleanup_completed`, and
`report_import.native_fallback_used`. Events contain attempt identity, an
abstract or hashed path reference, hash prefix, byte count, duration, backend
and contract versions, and result status. They contain no patient names,
extracted text, complete raw report, or secret key.

Verification covers native mutation races, truncation, links, non-regular and
unreadable sources, full targets, synchronization failures, SHA-256 parity, and
temporary-file cleanup. Python and database tests cover same-path and
same-content concurrency, at least eight distinct concurrent PDF imports,
crashes at publication boundaries, lease expiry, stale fencing tokens, retries,
one canonical `RawPdfFile`, one successful history record, a valid output PDF,
and post-reconciliation cleanup.

Continuous Integration builds from a clean checkout, runs Rust tests,
regenerates stubs with an empty diff, builds and installs the wheel in a fresh
environment, invokes the capability and snapshot functions from that wheel,
tests Python fallback parity, and exercises the oldest supported and current
`lx-anonymizer` versions. An unversioned local shared object is not evidence.

Implementation order and readiness remain in `Reporting.yml`. Every stage must
remain importable, leave existing stream endpoints unchanged, and retain a safe
rollback to the previous fully validated state.
