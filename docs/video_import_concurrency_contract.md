# Video Import Concurrency Contract

This document defines the implementation rules for concurrent video import
across `endoreg-db` and `lx-anonymizer`. The authoritative implementation and
production-readiness status remains
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml);
this document is an architecture and review reference, not a separate roadmap
or completion tracker.

The storage, timeline, publication, and cleanup rules in
[`video_storage_normalization.md`](video_storage_normalization.md) remain
mandatory. The presentation timestamp (PTS) and frames-per-second (FPS)
contract remains defined in
[`video_pts_fps_callsite_inventory.md`](video_pts_fps_callsite_inventory.md).

## Terms

- **Import attempt:** one durable execution identity for ingesting and
  anonymizing one immutable source generation.
- **Lease:** time-bounded ownership that a worker must renew while it performs
  work.
- **Fencing token:** a monotonically increasing integer issued with ownership.
  A worker with an older token may not change durable state or publish files.
- **Content identity:** the source byte length, modification time, stable
  filesystem identity, and Secure Hash Algorithm 256-bit (SHA-256) digest
  derived from one stable read.
- **Attempt staging:** an attempt-owned directory inside the approved encrypted
  storage boundary. Its contents are not published artifacts.
- **Publication:** the atomic transition that makes a completely validated
  artifact generation authoritative.
- **Global Interpreter Lock (GIL):** the lock used by the Python interpreter.
  Native operations that perform long independent input/output or
  compute-bound work should release it.
- **FFmpeg:** the media processing executable used for probing, masking,
  transcoding, and container publication.
- **JavaScript Object Notation (JSON):** the required structured log format.

## Ownership Boundary

| Concern | `endoreg-db` | `lx-anonymizer` |
| --- | --- | --- |
| Durable import-attempt state | Sole owner | Must not persist or infer it |
| Lease, heartbeat, and fencing token | Sole owner | Receives an invocation identity and cancellation signal |
| Content-hash deduplication | Sole owner | Must not create a competing lock |
| Encrypted storage routing | Sole owner | May access only caller-provided input and attempt output paths |
| Source and output profile validation | Sole owner | May provide observations, never publication authority |
| Clinical masking and metadata extraction | Orchestrates and validates | Sole compute owner |
| Temporary files created by anonymization | Allocates approved attempt directory | Owns only files beneath that directory for the invocation |
| Canonical master publication | Sole owner | Must return an unpublished candidate |
| Database writes | Sole owner | Prohibited |
| Retry and recovery policy | Sole owner | Must be deterministic and safe to invoke again |

No `lx-anonymizer` success return is sufficient to publish media. A candidate
must still pass the `endoreg-db` storage profile, timeline, hash, and clinical
workflow gates.

## Typed Attempt Contract

The orchestration service must normalize durable state into one typed internal
attempt object before invoking expensive work. The object must contain at
least:

- opaque attempt identifier;
- content SHA-256;
- source generation identifier;
- lease owner identifier;
- fencing token;
- lease expiry;
- attempt staging directory;
- immutable input path or already-open file descriptor;
- candidate output path;
- storage and anonymization profile versions;
- cancellation state;
- timestamps for creation, start, last heartbeat, and completion.

Free-form dictionaries and unvalidated persisted JSON payloads are prohibited.
Use a typed dataclass or Pydantic model in the service layer and validate the
persisted representation at the model boundary. The database model contains
persistence fields and constraints only; lease acquisition, renewal, recovery,
and publication remain service logic.

Every productive entry point must create or acquire this durable attempt before
calling `VideoImportService` or `ReportImportService`. This includes the upload
application programming interface (API), the regular watcher, the
pre-anonymized watcher, hub transfer receipt, management commands, and direct
service calls. An upload or transfer ledger may carry the fields itself; a
report attempt may use its dedicated attempt row. In either case the durable
record, not an in-memory context object, is the source of cluster ownership.

The `attempt_id` identifies one execution. The `source_generation_id`
identifies the immutable bytes it was authorized to read. Retrying after lease
expiry increments the fencing token and uses a new execution identity; it does
not revive the old worker. File locks, advisory locks, and process-local mutexes
only reduce duplicate work on one host. They never establish cross-host
ownership, never permit publication, and must not be reclaimed merely from file
age. Database time, the persisted lease owner, lease expiry, state, and fencing
token are authoritative.

### Wrapper heartbeat architecture

The production call boundary deliberately has two layers:

1. The entrypoint wrapper acquires a persisted import attempt and starts one
   background heartbeat for the complete expensive operation. It owns database
   clock renewal, the lease owner and fencing token, retry classification, and
   terminal attempt state.
2. The wrapper constructs one `VideoImportExecutionFence` containing the
   opaque attempt identifier and a synchronous `guard` capability, then calls
   `VideoImportService.import_and_anonymize_fenced`.
3. The video service calls that guard before durable state changes and
   publication checkpoints. The guard verifies current database ownership and
   also surfaces a renewal failure previously observed by the heartbeat.
4. When the service returns or raises, the wrapper performs its final fenced
   transition and stops the heartbeat.

The guard is not the heartbeat. Calling it does not replace periodic renewal;
it is the fail-closed bridge that lets deep processing code prove that the
wrapper still owns the attempt immediately before a mutation. Conversely, the
heartbeat does not grant publication authority by itself: every durable write
still needs a successful guard or an equivalent row-locked fencing check.

`VideoImportService.import_and_anonymize` is intentionally documented as the
unfenced compatibility path. Productive API, watcher, transfer, command, or job
wrappers must not call it. Keeping fenced invocation as a separate method
prevents an attempt identifier from being passed without a guard (or a guard
without its attempt identifier), which would create misleading partial
ownership. Migration is incomplete while any productive entrypoint still uses
the unfenced method; such paths must remain visible in the feature tracker and
must not be described as cluster-safe.

## Attempt State Machine

The durable state machine must be explicit and reject invalid transitions:

```text
queued
  -> hashing
  -> staging
  -> anonymizing
  -> validating
  -> publishing
  -> succeeded
```

Any active state may transition to `failed`, `cancelled`, `deferred`, or
`lost` when its documented invariant applies. Recovery creates or acquires a
new fenced ownership generation; it never silently revives the authority of an
expired worker.

Heavy hashing, copying, probing, anonymization, and FFmpeg execution must run
outside long database transactions. A short transaction may acquire ownership
or commit one state transition. The worker must then verify its fencing token
again before every durable state transition and immediately before
publication.

## Lease and Fencing Rules

1. At most one active attempt may own a content hash and publication target.
   Enforce this through a database constraint or an equivalently strong
   transactional invariant.
2. Filesystem locks are local optimizations only. They are not authoritative
   across hosts and must not be reclaimed solely because a wall-clock age was
   exceeded.
3. The owning worker renews its lease during long native, FFmpeg, or
   anonymization phases. Renewal failure requests cancellation and prevents
   further publication.
4. Each ownership acquisition receives a fencing token greater than every
   prior token for that attempt or content identity.
5. Every state mutation, candidate registration, and publication compares the
   supplied token with the current durable token.
6. An expired or superseded worker may clean only its own unpublished attempt
   staging. It may not remove a canonical artifact or another attempt's files.
7. Database time, not unsynchronized worker wall-clock time, determines lease
   expiry.

## Immutable Source Handoff

The import watcher must hand off a completed source using an atomic filesystem
operation into an attempt-owned path inside approved encrypted storage.

- The source must be a regular file and must not be a symbolic link.
- The handoff path is immutable. Producers must never append to or overwrite it
  in place.
- Content identity is derived only after local path ownership is acquired.
- Native hashing must compare the opened file identity before and after reading
  and compare it with the pathname still selected at completion.
- A detected mutation, replacement, truncation, or missing pathname fails
  loudly and preserves evidence for quarantine.
- The same verified identity is reused for deduplication and provenance rather
  than being recomputed through unrelated reads.
- A future native copy-and-hash operation must read an already-open descriptor,
  write only into attempt staging, synchronize the output, and return a typed
  identity. It must not publish or delete files.

All filesystem mutations continue to use
`endoreg_db.utils.filesystem.file_operations`, atomic replacement semantics,
and structured JSON logging.

## `lx-anonymizer` Invocation Rules

One invocation processes exactly one immutable source generation and produces
exactly one unpublished candidate plus typed metadata.

- A `FrameCleaner` instance is not currently safe to share across videos:
  frame collections, observations, language-model budgets, sensitive metadata,
  and current frame counts are mutable run state.
- Until this state is moved into a typed invocation object, create one
  `FrameCleaner` per attempt and do not call it concurrently.
- Long-lived model objects may later be shared only behind an explicitly
  documented thread-safe or process-safe interface. Mutable per-video state
  may never be stored on that shared object.
- `lx-anonymizer` must accept caller-selected input and output paths. It must
  not derive a shared output filename from the input when used by
  `endoreg-db`.
- The output path must be unique to the attempt, retain the media suffix needed
  by FFmpeg, and remain unpublished.
- Existing output, partial output, or metadata from another attempt must cause
  a loud collision error. Blind FFmpeg overwrite flags are permitted only for
  a path proven to be owned by the current attempt.
- Success requires an existing, non-empty candidate and typed metadata.
  `endoreg-db` remains responsible for complete media validation.
- Failure must terminate and reap child processes and report a typed error.
  It must not return `False` while leaving an ambiguous partial success.

The repository-specific rules are maintained in
the [`lx-anonymizer` component contract](https://github.com/wg-lux/lx-anonymizer/blob/main/docs/VIDEO_IMPORT_CONCURRENCY_CONTRACT.md).
That document may narrow implementation rules inside `lx-anonymizer`, but it
may not redefine durable ownership, publication, storage, retry, or cleanup.
If the documents conflict, this cross-repository contract and the feature YAML
prevail. A change to this boundary must update both documents and record the
paired repository revisions in review evidence.

## FFmpeg and Cancellation

Every FFmpeg operation must be owned by one invocation:

- start it in a controllable process group;
- retain the process handle at the workflow boundary;
- enforce a phase-specific timeout;
- capture bounded diagnostic output;
- on cancellation, lease loss, or timeout, request graceful termination and
  then force termination after a bounded grace period;
- wait for every child before returning;
- never reuse named pipes, temporary directories, or output names between
  attempts;
- validate the candidate after the process exits successfully.

Cancellation is cooperative during Python and native phases and enforced at
subprocess boundaries. Cancellation does not authorize deletion of any
published artifact.

## Native Acceleration Policy

Native Rust code is appropriate for bounded, typed operations such as stable
hashing, descriptor-to-staging copying, checksums, and large pure-data
transformations.

- Native input and output types must be represented in generated Python stubs.
- Long operations must release the Python GIL.
- Native errors that affect integrity must propagate; Python fallback must not
  reinterpret a mutation or integrity failure as success.
- The native module exposes a compatibility version and capability set.
- Production configuration may require an exact compatible native capability
  set and must fail startup if it is unavailable.
- Development fallback is named, observable, and tested. Every fallback use
  emits structured telemetry.
- Rust must not own Django persistence, leases, clinical state transitions,
  storage routing, publication, or cleanup policy.

## Resource Concurrency

Concurrency is permitted between independent attempts, not inside an
individual video's publication sequence.

- Hashing and bounded file input/output may run concurrently when storage
  measurements demonstrate headroom.
- FFmpeg, frame extraction, inference, and training retain separate worker
  queues and independently configured concurrency.
- Worker limits account for encrypted-storage throughput, temporary byte
  demand, memory, graphics processor capacity, and database connections.
- Queue admission reserves projected temporary storage before expensive work.
- Prefetch must not allow one worker to reserve multiple storage-heavy attempts
  invisibly.
- Load shedding produces an explicit deferred or capacity error state; it does
  not switch to a lower-integrity processing path.

### Snakemake batch orchestration

`workflow/Snakefile` is the optional macro-concurrency guard for explicitly
configured batch work. It does not replace Celery for online requests and does
not own database state. The batch graph can select:

1. a video import from a configured immutable source;
2. an optional processed-video storage-pressure transcode, selected either by
   a database video identifier or an upstream import receipt;
3. an optional raw or processed HTTP Live Streaming (HLS) materialization,
   selected by a database video identifier, import receipt, or transcode
   receipt.

Each rule invokes the existing Python service. Import leases, fencing,
protected storage routing, profile validation, atomic publication, media
operation leases, and cleanup therefore remain authoritative in the same
service paths used outside Snakemake. A successful import already requires raw
and processed HLS readiness. A later configured HLS rule is an idempotent
readiness or regeneration request, not a weaker replacement for that import
gate.

Stage handoff uses typed, atomically written JavaScript Object Notation (JSON)
receipts beneath the configured receipt directory. A downstream rule validates
the complete schema, stage, job identity, status, and expected source or
processed generation from its single declared upstream receipt. It then
re-loads the authoritative video from the database and rejects stale
generation provenance. Receipt metadata is never publication authority.

The `offline-batch` profile uses `forceall: true`. Every supervised invocation
therefore re-enters the idempotent services and reconciles database state; an
old completed receipt cannot suppress a readiness or regeneration check. The
same profile enables one retry, incomplete-job reruns, a bounded filesystem
latency wait, and fail-fast scheduling.

The workflow declares `mem_mb`, graphics processing unit (`gpu`), and
`rust_workers` as global resources
so local scheduling limits apply across the complete directed acyclic graph.
Operators must provide the actual host budget; rule declarations are
scheduling requirements, not runtime memory enforcement:

```bash
devenv shell -- snakemake \
  --snakefile workflow/Snakefile \
  --profile workflow/profiles/offline-batch \
  --cores 16 \
  --resources mem_mb=64000 gpu=1 rust_workers=16
```

The local workflow must not be run concurrently with an independently
scheduled batch that bypasses the same database leases and storage headroom
checks. Snakemake file locks protect its working directory, while database
leases and fencing remain the cross-process and cross-node authority.

Run the lane through its supervised entry point:

```bash
devenv shell -- python manage.py run_offline_batch \
  --config config/offline_batch_runner.yaml \
  --json
```

Snakemake's generic lint recommends per-rule Conda or container declarations.
This repository intentionally satisfies runtime reproducibility at the outer
boundary instead: the runner, Snakemake, Python services, native extension,
FFmpeg, and shared libraries execute from the pinned Devenv and uv lockfile
environment. Operators must start the command through `devenv shell`; an
unreviewed system-Python, Conda, or container fallback is not supported.

The versioned runner configuration supplies an approved local state path,
maximum runtime, shutdown grace period, heartbeat interval, and host-wide CPU,
memory, graphics processing unit, and Rust-worker budgets. Before Snakemake
starts, the runner loads the same typed workflow configuration and fails
closed if any configured rule requires more of one of those resources than
the host budget provides. The instance lock is process-owned, cannot be
reclaimed by age, and must remain inside the configured workflow root. It is a
local-host guard only; authoritative database leases and fencing remain the
cross-process and cross-node controls.

Startup is also fail-closed for explicitly configured native capability
contracts. Video imports require
`batch_file_identity/batch_file_identity_v1`. With the default
`assert_environment_readiness: true`, the supervisor invokes the existing
Django readiness gate for protected storage, permissions, media routing, and
production-required native contracts before starting Snakemake. Disabling
that check is an explicit development-only policy. Stage services still own
the authoritative per-attempt storage-headroom, lease, fencing, generation,
publication, and cleanup checks.

The runner starts Snakemake in a dedicated process group. A termination or
interrupt signal requests graceful process-group termination, waits only for
the configured grace period, escalates to forced termination when necessary,
and reaps the child before releasing the instance lock. The child starts with
a `0077` file-creation mask, protecting log files even if interpreter or
import failure occurs before stage code can enforce `0700` directory and
`0600` file modes. The maximum runtime uses the same bounded termination
sequence. Exit statuses are stable:

- `0`: completed;
- `75`: another local runner owns the instance lock;
- `124`: configured maximum runtime exceeded;
- `128 + signal number`: operator interruption;
- a nonzero Snakemake status: workflow failure.

Structured runner events are
`offline_batch.runner.lock_acquired`, `.lock_rejected`, `.started`,
`.heartbeat`, `.shutdown_requested`, `.termination_requested`,
`.termination_escalated`, `.completed`, and `.failed`. Every terminal event
contains the same random `batch_id` supplied to all stage receipts, a
`supervisor_config_sha256` digest of the runner configuration, the canonical
`workflow_config_sha256` digest used by stage receipts and calculated without
the per-run batch identifier, Coordinated Universal Time start and completion
timestamps, status, exit code, duration, and a zero-or-one failure counter.
Metrics fields expose starts, completions, failures, active heartbeats, lock
contention, shutdowns, termination requests, and forced termination. They
contain neither configuration contents nor source paths.
Alert at minimum on every `.failed`, `.lock_rejected`, and
`.termination_escalated` event, and on the absence of `.heartbeat` for longer
than the configured interval plus the monitoring system's ingestion margin.
The same terminal payload is strictly validated and atomically persisted with
mode `0600` beneath the configured `0700` summary directory for completion,
failure, interruption, timeout, and collision-safe lock rejection.

This remains an optional offline lane, not the online production owner.
Readiness still requires lease-deferral failure injection, a real
import-to-transcode-to-HLS end-to-end test, and a 20-to-50-video shadow pilot
measuring graphics processing unit utilization, peak memory,
protected-storage throughput, failure rate, and operator time. Resource
declarations are scheduler accounting and do not enforce an operating-system
memory limit.

### Thread-pool alignment

For each rule, `threads` is the total central processing unit (CPU) allocation. The configured
`rust_workers` and `ffmpeg_threads` must each be less than or equal to
`threads`. Snakemake can reduce `threads` to the cores available at execution,
so the stage script caps every inner pool again at the effective
`snakemake.threads` value and exports:

- `RAYON_NUM_THREADS` for libraries that use Rayon's global pool;
- `LX_ANNOTATE_HLS_FFMPEG_THREADS` for the HLS encoder;
- `OMP_NUM_THREADS` for Open Multi-Processing (OpenMP) and
  `MKL_NUM_THREADS` for Intel Math Kernel Library scientific runtimes.

`BatchProcessor` does not use Rayon's process-global pool. It owns a private
pool with the explicit effective `rust_workers` count, and its parallel file
identity method releases the Python Global Interpreter Lock for the complete
Rust-only operation. It never calls Python from a Rayon worker. Returned rows
retain input order, while any file mutation, replacement, truncation, or input
error fails the whole batch.

Do not allocate `threads=N` to a Snakemake rule and independently give Rayon,
FFmpeg, OpenMP, and an artificial-intelligence runtime `N` active workers at
the same time. When engines overlap, partition the rule's total allocation
between them or serialize their phases. The current video stages serialize
native identity work, anonymization/transcoding, and HLS publication. This
keeps the maximum active CPU pool bounded by the rule allocation and avoids
oversubscription. Waiting for a Python callback from inside a GIL-detached
Rayon operation is prohibited because it can introduce lock-order deadlocks.

## Required Structured Events

Logs and metrics must identify the attempt without exposing patient data or
raw media paths outside approved logging policy. Required events include:

- lease acquired, renewed, lost, expired, and superseded;
- stable identity started, completed, backend selected, bytes read, and
  mutation rejected;
- duplicate content joined or short-circuited;
- staging reserved and released;
- anonymization and FFmpeg phase started, completed, cancelled, timed out, and
  failed;
- candidate validation passed or failed;
- publication accepted or rejected by fencing;
- cleanup completed, deferred, or rejected by ownership;
- native fallback selected.

Measure lease wait, hash duration, copy duration, anonymization duration,
validation duration, publication duration, bytes processed, temporary bytes,
and active attempts by phase.

## Verification Matrix

The implementation is not complete until stable tests demonstrate:

1. two processes importing the same path execute heavy work once;
2. different paths with identical content execute heavy work once;
3. independent content hashes progress concurrently;
4. mutation, truncation, replacement, and symbolic-link substitution fail
   closed;
5. an expired worker cannot transition state or publish after a new fencing
   token is issued;
6. worker termination in every active phase leaves a resumable or explicit
   `lost` state and no published partial output;
7. disk exhaustion preserves the prior valid generation;
8. cancellation terminates and reaps all FFmpeg children and named-pipe
   helpers;
9. repeated invocation with the same attempt identity is idempotent;
10. one `FrameCleaner` per attempt does not leak metadata, frame observations,
    or language-model budgets between concurrent processes;
11. production startup rejects a missing or incompatible required Rust
    capability;
12. database and filesystem reconciliation rejects unknown ownership.

Thread tests alone are insufficient. The suite must include process-level
tests and failure injection because production workers, FFmpeg, and native code
cross process boundaries.

## Implementation Order

Implement the contract in dependency order:

1. typed attempt and lease state with database constraints;
2. lease acquisition, heartbeat, fencing, and transition services;
3. immutable watcher handoff and attempt staging;
4. attempt-local `FrameCleaner` invocation and typed result;
5. cancellable FFmpeg process ownership;
6. fenced validation and atomic publication;
7. native capability negotiation and production gate;
8. process-level failure and load tests;
9. metrics, alerts, and staged production rollout.

Do not enable destructive cleanup or broaden queue concurrency until fencing,
failure injection, storage headroom, and reconciliation have passed their
feature-tracker gates.
