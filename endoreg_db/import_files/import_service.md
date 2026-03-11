# File Import and Anonymization

Endoreg-db imports are guarded by anonymization so sensitive patient data does not leave managed storage in raw form. For video imports there are three distinct phases:

1. Acquire a lock on the watched import file.
2. Create a sensitive working copy inside managed storage.
3. Standardize once, then reuse the standardized raw video everywhere else.

The main orchestration entrypoints are:

- `ReportImportService`
- `VideoImportService`

## Video Import Execution Order

The file watcher observes `data/import/video_import`. Once a file is stable, it calls `VideoImportService.import_and_anonymize(...)`.

The current intended flow is:

1. `ctx.file_path` points to the watched import file.
2. `ctx.original_path` is set to the same watched import file.
3. A `file_lock` is acquired on the watched import file.
4. Inside the lock, `create_sensitive_copy(...)` copies the import file into `SENSITIVE_VIDEO_DIR` under the original filename.
5. `create_or_retrieve_video_file(...)` consumes `ctx.sensitive_path`, not the watched import path.
6. `VideoFile.create_from_file_initialized(...)` is the single place that calls `transcode_videofile_if_required(...)`.
7. The standardized raw file is stored under the canonical hash-based filename in sensitive storage.
   It is first written to a `.part` path in the destination directory and atomically promoted only after verification.
8. The anonymizer reads from the canonical raw file path when available.
9. The anonymized output is stored under the canonical hash-based filename in anonymized storage.
   It is also written to a `.part` path first and atomically promoted into place.
10. The transient sensitive working copy is deleted after successful finalization.

## Steady-State Video Artifacts

After a successful import/anonymization cycle, the intended durable state is exactly two managed video files:

- One canonical raw sensitive video in sensitive storage.
- One canonical anonymized video in anonymized storage.

The watched import file may still exist until the file watcher removes it at the end of `_process_video(...)`. The transient sensitive working copy under the original filename is not intended to survive successful finalization.

## File Lock

`file_lock(path)` creates a sibling lock file named `<path>.lock`.

For video imports, the lock must be placed on the original watched import file, not on the sensitive working copy, because:

- The file watcher deduplicates work by the watched import path.
- Cross-process contention is about who owns the import event for that watched file.
- The watcher removes the watched import file after successful processing.

Locking the internal sensitive copy instead of the watched import file weakens coordination between independent watcher/service processes.

## Concurrency Model

The concurrency model is intentionally split:

- External coordination key: the watched import file path.
- Internal processing source: the sensitive working copy, followed by the canonical raw `VideoFile` path.

This means a worker first claims the watched import file, then performs internal storage operations from managed storage.

## Concurrency Risks

The main concurrency and consistency risks in this flow are:

- Duplicate watcher events in the same process.
  The file watcher uses an in-memory `processed_files` set. This avoids local duplicate work but does not protect against multiple service processes.

- Duplicate watcher events across processes.
  The real protection is `file_lock(...)` on the watched import path. If two processes see the same file, only one should acquire the lock before import proceeds.

- Copy-before-lock race.
  If a sensitive copy is created before the import lock is acquired, two workers can both copy the large import file into managed storage before one of them loses the lock race. This wastes IO and can create confusing temporary artifacts. The current implementation avoids this by copying only after the import lock is acquired.

- Duplicate standardization.
  If the pipeline standardizes during the sensitive copy step and again during `VideoFile` creation, the same 18GB source can be transcoded twice. The current intended design is "standardize once, consume everywhere": only `create_from_file_initialized(...)` performs standardization.

- Stale source paths after canonicalization.
  `create_from_file_initialized(...)` may move or copy the managed working file into the canonical raw path. Code that continues reading from `ctx.file_path` or `ctx.sensitive_path` after that can end up using a stale path. The anonymizer should prefer `ctx.current_video.get_raw_file_path()`.

- Leftover temporary sensitive copies.
  If the temporary sensitive working copy is not removed after success, the system can retain an extra original-named file in addition to the canonical raw file and the anonymized file. That inflates storage usage and makes the durable state ambiguous.

- Stale lock reclamation.
  `file_lock(...)` can reclaim locks older than `STALE_LOCK_SECONDS`. If a genuinely long-running job exceeds that threshold, another worker could reclaim the lock and start overlapping work. This is a real residual risk for very large videos and should be monitored operationally.

- Success/failure cleanup races.
  Cleanup code is best-effort. If a worker crashes after writing one artifact but before finalizing database state, the next retry path must be able to reconcile orphaned files and records.

## Startup Reconciliation

`ReconciliationService` runs once during application startup.

Its responsibilities are:

- Remove stale `.lock` files older than `STALE_LOCK_SECONDS`.
- Remove orphaned startup artifacts such as `.tmp`, `.part`, and UUID-named temporary files from managed storage.
- Reset database rows stuck in `processing_started=True` without a completed anonymization result.

This janitor makes the ingestion pipeline retry-tolerant after crashes, but it is still not a full distributed transaction protocol. The original import file remains the final retry source of truth until the watcher deletes it after a successful end-to-end run.

## Error Cleanup

Failure cleanup is responsible for removing transient anonymized, sensitive, and transcoding artifacts so retries start from a predictable state. Cleanup runs after failed processing and should not remove the canonical raw/anonymized pair created by a successful import.
