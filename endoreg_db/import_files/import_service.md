# File Import and Anonymization

This file is an entry point. The canonical video import, storage, and lifecycle contract is [`docs/video_storage_normalization.md`](../../docs/video_storage_normalization.md), and current production-readiness evidence is maintained in [`feature-tracking/VideoStorageNormalization.yml`](../../feature-tracking/VideoStorageNormalization.yml).

## Current entry points

The supported import orchestrators are:

- `ReportImportService`
- `VideoImportService`
- `endoreg_db.services.video_files.create_initialized_video_file_from_path(...)`

Local file-watcher ingestion and authenticated API upload both converge on shared ingest services and persisted upload jobs. The watcher implementation lives in the `lx-annotate` repository; this package owns the import and media-persistence services.

## Current safety contract

- Raw media remains inside the approved protected storage boundary and must never be exported.
- A video publishes exactly one canonical anonymized master generation. Raw media, streamable MPEG-4 Part 14 (MP4), HTTP Live Streaming (HLS), extracted frames, and transcode staging files have separate lifecycle roles.
- The versioned storage profile validates resolution, frame rate, bitrate, byte budget, codec, pixel format, duration, and timeline constraints. Invalid media fails loudly or enters an explicit quarantine path.
- Persisted presentation timestamps are authoritative for clinical segment and frame identity. Storage normalization preserves the source timeline.
- Publication is atomic, and cleanup must preserve the previous valid generation until validation, integrity, lease, and reconciliation gates succeed.
- New filesystem mutation code must use the typed helpers exposed through `endoreg_db.utils.filesystem.file_operations` and emit structured JavaScript Object Notation (JSON) logs. The normalization tracker records remaining migration evidence.

## Producer handoff

Producers that place files into a watched directory must write to a temporary name outside the watched final-name pattern, flush and close the file, and atomically rename it to the final name only when complete. The current Python helper is `endoreg_db.utils.file_operations.atomic_handoff_file(...)`; it remains on the legacy import surface while filesystem helpers migrate to the canonical package.

For exact execution order, leases, cleanup gates, presentation-timestamp rules, and operational recovery, follow the canonical runbook and its feature-tracker evidence rather than duplicating those details here.
