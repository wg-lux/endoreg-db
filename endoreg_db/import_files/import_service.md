# File Import and Anonymization

Endoreg-db imports are guarded by anonymization so sensitive patient data does not leave managed storage in raw form. For video imports there are three distinct phases:

1. Acquire a lock on the watched import file.
2. Create a sensitive working copy inside managed storage.
3. Standardize once, then reuse the standardized raw video everywhere else.

The main orchestration entrypoints are:

- `ReportImportService`
- `VideoImportService`
- `endoreg_db.services.video_files.create_initialized_video_file_from_path(...)`

## Video Import Execution Order

The file watcher observes `data/import/video_import`. Once a file is stable, it calls `VideoImportService.import_and_anonymize(...)`. Producers must not stream bytes directly into a watched final filename such as `*.mp4`; direct writers use a temporary handoff name first and atomically rename only after the file is closed and flushed.

The current intended flow is:

1. `ctx.file_path` points to the watched import file.
2. `ctx.original_path` is set to the same watched import file.
3. A `file_lock` is acquired on the watched import file.
4. Inside the lock, `create_sensitive_copy(...)` copies the import file into `SENSITIVE_VIDEO_DIR` under the original filename.
5. `create_or_retrieve_video_file(...)` consumes `ctx.sensitive_path`, not the watched import path.
6. `create_initialized_video_file_from_path(...)` is the single service entrypoint that calls `transcode_videofile_if_required(...)`.
7. The standardized raw file is stored under the canonical hash-based filename in sensitive storage.
   It is first written to a `.part` path in the destination directory and atomically promoted only after verification.
8. The anonymizer reads from the canonical raw file path when available.
9. The anonymized output is stored under the canonical hash-based filename in anonymized storage.
   It is also written to a `.part` path first and atomically promoted into place.
10. The transient sensitive working copy is deleted after successful finalization.

## Producer Atomic Handoff Contract

External producers and uploaders that bypass the lx-annotate file watcher must use an atomic handoff pattern:

1. Write into a name outside the watched final pattern, for example `exam.mp4.part` or `exam.mp4.tmp`.
2. Finish writing, call `flush()` and `fsync()` on the file descriptor, close the file, and fsync the containing directory when the platform allows it. Producers in Python should use `endoreg_db.utils.filesystem.file_operations.atomic_handoff_file(...)`.
3. Promote the completed file with an atomic same-filesystem rename to the final watched name, for example `exam.mp4`.
4. Never append to or rewrite the final watched `*.mp4` after rename.

The hub ingest service defensively ignores in-progress suffixes such as `.tmp`, `.part`, `.partial`, `.crdownload`, and `.download`, including marker names such as `.tmp.` and `.part.`. It also performs its own settle check before hashing and before persisting a watcher `UploadJob`, so direct service callers get deferred/retry behavior instead of capturing a partially written file.

The video import pipeline uses one verified local raw materialization for the VideoMeta/ffprobe validation immediately preceding anonymization and for the `FrameCleaner.clean_video(...)` input. The anonymizer logs the exact absolute path, byte size, hash, and stream dimensions. If this exact input no longer matches the validated metadata source, processing aborts and the mismatched input is copied to quarantine.

## Filewatcher Operation Ledger

The filesystem watcher lives in `lx-annotate` at
`lx_annotate/file_watcher.py` and calls the hub ingest service in this
repository. The watcher keeps filesystem-based dropoff ingestion available for
trusted local workflows and delegates concrete processing to the same import
services used elsewhere:

- `VideoImportService.import_and_anonymize(...)`
- `ReportImportService.import_and_anonymize(...)`

### Video copy and transcode operations

For one video dropped into `data/import/video_import`, the current pipeline performs these file operations in order:

1. Sensitive staging copy.
   `create_sensitive_copy(...)` copies the watched import file into `SENSITIVE_VIDEO_DIR` under the original filename via `atomic_copy_with_fallback(...)`.
2. Canonical raw video standardization.
   `create_initialized_video_file_from_path(...)` calls `transcode_videofile_if_required(...)` with the sensitive staging copy as input and a `.part` file in canonical sensitive storage as output.
3. FFmpeg transcode when the source is not compliant.
   If codec is not `h264`, pixel format is not `yuv420p`, or color range is not `pc`, FFmpeg writes a transcoded file to the `.part` path.
4. Plain file copy when the source is already compliant.
   If no transcode is required and the canonical output path differs from the input path, `transcode_videofile_if_required(...)` still copies the file into the `.part` path with `shutil.copy2(...)`.
5. Atomic promotion into canonical raw storage.
   The `.part` file is promoted with `os.replace(...)` to `SENSITIVE_VIDEO_DIR/<video_hash><original_suffix>`.
6. Anonymizer output write.
   `VideoAnonymizer.anonymize_video(...)` reads from the canonical raw path when available and writes anonymized output to `ANONYM_VIDEO_DIR/<video_hash>.part.mp4`.
7. Atomic promotion into final anonymized storage.
   The anonymizer promotes its temp result to `ANONYM_VIDEO_DIR/<video_hash>.mp4` using `os.replace(...)`.
8. Final move safety net.
   `finalize_video_success(...)` moves `ctx.anonymized_path` to the same canonical anonymized target if the anonymizer did not already write there.
9. Cleanup of transient copies.
   `finalize_video_success(...)` deletes the original-name sensitive staging copy if it is not the canonical raw file and clears the transcoding directory.

In the normal successful path, a compliant video causes two full-size copies and zero FFmpeg transcodes:

- import source -> original-name sensitive staging copy
- sensitive staging copy -> canonical raw video `.part` -> canonical raw video

In the normal successful path, a non-compliant video causes one copy plus one FFmpeg transcode:

- import source -> original-name sensitive staging copy
- sensitive staging copy -> FFmpeg transcode -> canonical raw video `.part` -> canonical raw video

After that, the anonymizer writes one more full anonymized output file.

### Report copy operations

For one report dropped into `data/import/report_import`, the current pipeline performs these file operations:

1. Optional txt-to-pdf materialization.
   If the watched file ends in `.txt`, `_create_temp_pdf_from_txt(...)` renders a temporary single-page PDF in the system temp directory.
2. Sensitive staging copy of the original payload.
   `create_sensitive_copy(...)` copies the original report source into `SENSITIVE_REPORT_DIR` under the original filename.
   For `.txt` inputs this copies the original `.txt`, not the temporary PDF.
3. Canonical raw report save into Django-managed storage.
   `RawPdfFile.create_from_file_initialized(...)` opens `ctx.file_path` and saves it through Django storage using a generated content-hash-based filename.
   For PDF input, this means the imported PDF is copied from the watched location into managed raw storage.
   For TXT input, this means the temporary rendered PDF is copied from `/tmp/...pdf` into managed raw storage.
4. Optional restoration copy for pre-existing records.
   If a `RawPdfFile` record already exists but its stored file is missing, `create_from_file(...)` re-saves the source file into storage from the current import path.
5. Anonymized report write.
   `ReportAnonymizer.anonymize_report(...)` asks `lx_anonymizer.ReportReader.process_report(...)` to write `ANONYM_REPORT_DIR/<pdf_hash>.pdf`.
6. Final move safety net.
   `finalize_report_success(...)` moves `ctx.anonymized_path` into `ANONYM_REPORT_DIR/<pdf_hash>.pdf` only if the anonymizer wrote somewhere else.
7. Cleanup of transient files.
   Success cleanup deletes the original-name sensitive staging copy if it is not the canonical raw file.
   TXT imports also delete the original `.txt` once the managed record exists and delete the temporary rendered PDF in the `finally` block.

There is no report transcoding step in the current report import path. The only format conversion is `.txt` -> temporary generated `.pdf`.

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
  If the pipeline standardizes during the sensitive copy step and again during `VideoFile` creation, the same 18GB source can be transcoded twice. The current intended design is "standardize once, consume everywhere": only `create_initialized_video_file_from_path(...)` performs standardization.

- Stale source paths after canonicalization.
  `create_initialized_video_file_from_path(...)` may move or copy the managed working file into the canonical raw path. Code that continues reading from `ctx.file_path` or `ctx.sensitive_path` after that can end up using a stale path. The anonymizer should prefer `endoreg_db.services.video_files.get_raw_video_file_path(ctx.current_video)`.

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
