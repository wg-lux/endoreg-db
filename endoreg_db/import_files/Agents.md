# Agents & Import Orchestration

This document describes the long-running “agents” in the system that handle
media imports, anonymization and cleanup in the background. It also captures
the design principles and practical rules we follow when adding new agents or
import flows.

---

## 1. What is an “agent” in this project?

In this codebase an **agent** is any long-running, non-interactive component
that:

- Watches for new work (files, messages, DB rows, etc.)
- Executes a well-defined pipeline (import → anonymize → persist → cleanup)
- Runs without direct user interaction (daemon / service / worker)

Examples:

- File watcher / importer that reacts to new PDFs and videos
- Report Import Service (RIS)
- Video Import Service (VIS)
- Future import services (images, telemetry, etc.)

Agents are strictly **back-end executors**: they never talk to users directly
and they must always be safe to run on a schedule or be restarted.

---

## 2. High-level design goals

All agents and import services are designed with these goals in mind:

1. **Privacy & pseudonymization first**  
   - All raw inputs (PDFs, videos) are treated as potentially sensitive.
   - Anonymization / pseudonymization happens as early as possible in the
     pipeline.
   - Fake / pseudonymous patients are generated to decouple *storage* from
     real identities.

2. **Idempotent, restartable jobs**  
   - A job can be retried without corrupting state or duplicating imports.
   - Partial work is either cleaned up or brought to a consistent “failed”
     state that can be retried.

3. **Single-responsibility agents**  
   - Each agent has a small, clear job (e.g. “import PDFs”, “import videos”).
   - Cross-cutting concerns (locks, error cleanup) live in shared helpers, not
     copy-pasted into each agent.

4. **Explicit lifecycle**  
   - Initialize context  
   - Acquire file lock  
   - Validate input  
   - Import & anonymize  
   - Finalize state  
   - Cleanup & release lock  

5. **Observability & debuggability**  
   - Each stage logs *what* it is doing and *which file / video / pdf_hash /
     uuid* is being processed.
   - Errors are logged with enough context to reproduce and understand the
     failure.

---

## 3. Components & responsibilities

### 3.1 File lock

**Module:** `file_lock.py`  
**Responsibility:** ensure that at most one agent processes a given file at a time.

Behavior:

- For a given path, a `<filename>.lock` file is created when processing starts.
- The lock is held for the entire pipeline (import → anonymize → cleanup).
- Lock files have a **stale timeout**; stale locks can be reclaimed so the
  system doesn’t get stuck forever on a crashed worker.
- The lock is removed only after:
  - Import / anonymization has finished **or**
  - Error cleanup has run

Key rules:

- Lock acquisition must be **atomic** for a single path.
- All import services (PDF, video, future media) use the same lock mechanism.
- Never bypass the file lock in production code.

### 3.2 Error cleanup agents

**Modules:**  

- `report_cleanup_on_error.py`
- `video_cleanup_on_error.py`

**Responsibility:** revert or clean up partial work when an import fails.

For each media type we have a dedicated cleanup function that:

- Knows how to restore or delete partial files (raw / sensitive / anonymized).
- Resets state flags on the corresponding state model (`RawPdfState`,
  `VideoState`).
- Cleans up temporary directories or stray copies created during import.
- Updates internal bookkeeping (`processed_files` etc.) so retries are
  possible.

These are **not** standalone processes; they are called by the import services
while the file lock is still held.

### 3.3 Report Import Service (RIS)

**Module:** `pdf_import.py` (PdfImportService)

Responsibilities:

- Import PDFs dropped into the PDF ingest directory.
- Create / update `RawPdfFile` and `SensitiveMeta` entries.
- Delegate text extraction and anonymization to `lx_anonymizer` (report
  reader).
- Ensure **default pseudonymous patient data** exists if nothing was extracted.
- Persist anonymized PDFs and metadata, update `RawPdfState`.

Pipeline outline:

1. Acquire file lock for this PDF.
2. Initialize processing context (paths, center, flags, hashes).
3. Create or retrieve `RawPdfFile` instance (deduplicate by hash).
4. Move original file to “sensitive” storage.
5. Run text & metadata extraction + anonymization via `ReportReader`.
6. Update text fields and `SensitiveMeta` (respecting overwrite rules).
7. Update state: mark anonymized / ready for validation.
8. On error, call **report error cleanup** while lock is still held.
9. Release lock and reset context.

### 3.4 Video Import Service (VIS)

**Module:** `video_import.py` (VideoImportService)

Responsibilities:

- Import videos dropped into the video ingest directory.
- Create / update `VideoFile`, `SensitiveMeta` and `VideoState`.
- Move raw video into `/data/videos` and a sensitive copy into
  `VIDEO_DIR/sensitive`.
- Extract frames and initialize frame objects.
- Run frame-level anonymization using ROIs from `EndoscopyProcessor` via
  `lx_anonymizer`’s `FrameCleaner`.
- Fallback to a simple copy when advanced anonymization fails.
- Archive anonymized video in `/data/anonym_videos`.

Pipeline outline:

1. Acquire file lock for this video.
2. Initialize processing context (paths, center, processor, flags).
3. Validate input and ensure not already processed.
4. Create `VideoFile` and move raw video to final storage.
5. Initialize specs, extract and register frames.
6. Ensure default pseudonymous patient data exists (or mark meta as processed).
7. Run frame cleaning (ROI masking) with timeout and fallbacks.
8. Finalize state (`VideoState`) based on success or failure.
9. Move cleaned / fallback video to anonymized storage.
10. On error, call **video error cleanup** while lock is still held.
11. Release lock and reset context.

---

## 4. Agent lifecycle & control flow

Every import-type agent should follow a **shared lifecycle**:

1. **Discover work**
   - File watcher, message queue, or explicit CLI call passes a `file_path`
     and associated metadata (center, processor, etc.) to the service.

2. **Acquire file lock**
   - `with file_lock(path, file_type=...)` ensures only one agent processes
     this path.
   - If the file is already being processed and lock is not stale, the agent
     should log and **skip** cleanly.

3. **Initialize processing context**
   - Central dict that holds:
     - Paths (`file_path`, `raw_video_path`, `sensitive_file_path`, etc.)
     - Flags (`processing_started`, `frames_extracted`, `anonymization_completed`)
     - Error reason for logging / UI

4. **Validate & prepare input**
   - Check file existence and type.
   - Deduplicate based on media hash when possible.
   - Set up directories and storage paths.

5. **Run import + anonymization**
   - Media type specific (RIS vs. VIS), but always:
     - Do the minimal required work to reach a safe, pseudonymous state.
     - Handle third-party failures (OCR, LLM, ffmpeg, etc.) with clear fallbacks.

6. **Finalize state**
   - Mark appropriate flags on `RawPdfState` / `VideoState`.
   - Save models inside a transaction where possible.

7. **Cleanup / archive**
   - Move final anonymized output to canonical storage.
   - Drop temporary data (frames, cropped regions) if no longer needed.
   - Optionally trigger downstream notifications (“ready for validation”).

8. **Error handling & cleanup**
   - On any exception:
     - Log with context (file identifiers, center, step).
     - Call the appropriate error cleanup helper for this media type.
     - Let the exception bubble up to the caller / supervisor so failures
       are visible.

9. **Release lock and reset**
   - File lock is released after cleanup.
   - Service local state (`current_*`, `processing_context`) is reset to avoid
     cross-contamination between jobs.

---

## 5. Best-practice rules for new agents

When adding a new agent or media import, follow these rules:

1. **Always use the file lock**  
   - Never bypass the lock for production imports.  
   - If you add a new media type, add a corresponding `file_type` value and
     reuse the same lock mechanism.

2. **Make the pipeline idempotent**
   - It should be safe to rerun the whole import for the same file without:
     - Duplicating DB rows.
     - Leaving conflicting files (two anonymized copies with different names).
   - Prefer “check-then-short-circuit” over re-processing when state is
     already complete.

3. **Never trust external tools blindly**
   - Treat OCR, ffmpeg, LLMs and any external binaries as **fallible**:
     - Validate their outputs.
     - Time out long-running tasks.
     - Implement fallbacks (e.g. “import but mark as not anonymized”).

4. **Separate orchestrators from implementation details**
   - Keep the orchestration (agent loop, locks, lifecycle) separate from:
     - Media-specific transformations.
     - Third-party integrations.
   - Aim for small, testable helper functions that do one thing.

5. **Centralize cross-cutting concerns**
   - Locking, cleanup, storage path resolution, and helper utilities should
     live in shared modules rather than being duplicated per agent.

6. **Log like you’ll forget everything tomorrow**
   - Log at least:
     - Start and end of each major step.
     - File identifiers (hash, uuid) and center.
     - Error reasons in a way that is searchable.
   - Avoid logging raw PHI; log pseudonymous IDs instead.

7. **Design for cluster-safe execution (eventually)**
   - Even if you currently run on a single node, avoid assumptions that break
     in multi-process / multi-host setups:
     - No in-memory “this file is already processed” flags as the only guard.
     - Prefer storage-based signals (lock files, DB flags) that work across
       processes.

---

## 6. Operational notes

- Agents should be managed by a supervisor (systemd, container runtime, or a
  queue worker process) with:
  - Auto-restart on crashes.
  - Centralized logs.
- Import services must fail **loudly** (exceptions, non-zero exit codes, or
  explicit error records) so that monitoring can alert on failures.
- Manual reprocessing of a file should go through the same pipeline as
  automatic imports to guarantee identical behavior.

---

## 7. Extending the system

When introducing a new media type (e.g. images):

1. Define a new `Model` + `State` + optional `SensitiveMeta` relation.
2. Implement a dedicated import service that follows the lifecycle described
   above.
3. Reuse:
   - File lock
   - Error cleanup pattern
   - Storage layout conventions
4. Add tests that:
   - Simulate successful imports.
   - Simulate failures at different stages and validate cleanup behavior.
   - Verify idempotency (re-importing the same file is safe).

This keeps all agents consistent, debuggable and safe in the face of real-world
failures while enforcing pseudonymization as a hard rule instead of a nice-to-have.
