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
`lx-anonymizer/docs/VIDEO_IMPORT_CONCURRENCY_CONTRACT.md`.

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
