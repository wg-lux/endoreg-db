# Electrosurgical measurements on the video timeline

Scope, acceptance criteria and production readiness are tracked only in
`feature-tracking/ElectricalToVideoMapping.yml`. This document describes
the current host implementation; it carries no completion status. The canonical
algorithm, frame statuses and contracts are defined by lx_dtypes in
`lx-data-models/docs/guides/electrosurgical-video-alignment.md`.

**Not production-approved.** No device support matrix, synchronization profile
or clinically approved aggregation profile has been signed off. Every tolerance
used here is supplied by the importing operator.

## Terms

- **High-frequency (HF) electrosurgery:** a generator delivers radio-frequency
  current for cutting and coagulation; its measurements use the generator clock.
- **Presentation timestamp (PTS):** integer display time of a video frame in a
  rational time base. PTS define frame identity; nominal frames per second (FPS)
  are never used.
- **Sample:** one generator measurement at one integer tick of the generator
  clock. **Sample rate**, **clock drift**, **synchronization anchor** and
  **mapping residuum** follow the lx_dtypes guide.
- **Generation:** the published canonical processed video identified by
  `VideoFile.processed_video_hash`.

## Data model

`VideoElectrosurgicalMeasurement`
(`endoreg_db/models/media/video/electrosurgical_measurement.py`, migration
`0119_video_electrosurgical_measurement`):

| Field | Meaning |
| --- | --- |
| `video` | Owning video (`PROTECT`: deletion must handle the encrypted source explicitly) |
| `source_file` | Verbatim `ElectrosurgicalMeasurementSeries` JSON, ciphertext through `LazyEncryptedStorage` |
| `payload_sha256` | SHA-256 of the stored plaintext; re-verified on every read |
| `source_sha256`, `parser_id`, `parser_version`, `schema_version` | Provenance copied from the series |
| `recording_content_hash` | `VideoFile.raw_video_hash` when imported |
| `session_id`, `electrical_clock_id`, `time_base_num/den` | Generator clock identity |
| `sample_count`, `first_/last_sample_timestamp` | Bounds, no sample values |
| `channels` | Channel identifiers, quantities and UCUM units (validated at save) |
| `aggregation_profile` | `ElectricalFrameAggregationProfile` (validated at save) |

Rows are immutable and unique per `(video, source_sha256)`. Samples are never
stored in SQL JSON: the encrypted file is the single copy. Anchors are stored by
the existing `record_video_synchronization` service in `VideoFile.meta`.

## Import boundary

`import_electrosurgical_measurement` (`endoreg_db/services/electrical/measurement_import.py`),
operator entrypoint:

```text
python manage.py import_electrosurgical_measurements --video-id 42 \
    --series series.json --profile profile.json [--synchronization sync.json]
```

1. The series document (limit 64 MiB) is validated once with
   `ElectrosurgicalMeasurementSeries.model_validate_json`. Errors report field
   locations only, never values.
2. Supplied anchors go through `record_video_synchronization`, which rejects a
   different recording or conflicting anchors.
3. Under a row lock on the video: anchors must be bound, and session, clock and
   electrical time base must equal the series'.
4. The same `source_sha256` with identical bytes, profile and recording is a
   no-op; any difference is rejected. A new import writes the ciphertext first
   and removes it if the row cannot be saved.
5. Structured `electrosurgical_measurement_import` events carry identifiers,
   hashes and counts, never values.

## Alignment

On demand per request, never persisted or cached: Django's cache backend lies
outside the encrypted storage boundary, and recomputation from the current
identities makes reuse of a stale alignment impossible. `align_measurement_frames`
(`endoreg_db/services/electrical/video_alignment.py`):

1. Refuses unless the requested generation hash equals the published one and
   the recording hash equals the one bound at import.
2. Resolves verified ancestry with `resolve_video_provenance`. An acknowledged
   `VideoProvenanceRecovery` (`electrical_matching_eligible=false`), missing or
   ambiguous ancestry, or missing anchors refuses alignment.
3. Decrypts the series through the storage backend, re-verifies its hash and
   identities, optionally restricts it to requested channels, and calls
   lx_dtypes `align_electrosurgical_measurements` for one bounded frame range.
4. Adds `presentation_time_seconds` per frame with the canonical
   `presentation_timestamp_seconds` helper, the same conversion used by segment
   coordinates.

The host has no exact exclusive recording end in recording PTS ticks, so
`recording_end_timestamp` is not passed and the last output frame is reported as
`unbounded_interval` rather than estimated.

## API

Both routes are `GET`, require authentication and `CenterScopedVideoPermission`,
and use snake_case only.

### `media/videos/<pk>/electrical-measurements/`

```json
{
  "video_id": 42,
  "generation_content_hash": "b…",
  "timeline_version": "pts_v1",
  "algorithm": "electrical_frame_window_v1",
  "electrical_matching_eligible": true,
  "ineligibility_code": null,
  "ineligibility_reason": null,
  "total_frame_count": 90000,
  "max_frames_per_request": 5000,
  "measurements": [
    {
      "id": 3, "source_sha256": "e…", "schema_version": "1",
      "parser_id": "vendor_export", "parser_version": "1",
      "session_id": "1…", "electrical_clock_id": "3…",
      "time_base_num": 1, "time_base_den": 1000, "sample_count": 20000,
      "channels": [{"channel_id": "power", "quantity": "power", "unit": "W"}],
      "profile": {"profile_id": "…", "profile_version": 1, "…": "…"},
      "imported_at": "2026-11-20T10:00:00+00:00"
    }
  ]
}
```

`ineligibility_code` is one of `provenance_unresolved`,
`provenance_unavailable` or `synchronization_missing`.

### `media/videos/<pk>/electrical-measurements/<measurement_id>/frames/`

Query: `generation_content_hash` (required), `first_frame` (default 0),
`frame_count` (1 to 5000, default 5000), `channel_id` (repeatable, optional).
Frames are ordered by output frame index over the half-open range
`[first_frame, first_frame + frame_count)`, clipped at the last frame.

```json
{
  "video_id": 42,
  "measurement_id": 3,
  "timeline_version": "pts_v1",
  "alignment": {
    "algorithm": "electrical_frame_window_v1",
    "profile": {"…": "…"},
    "generation_content_hash": "b…", "generation_time_base_num": 1, "generation_time_base_den": 90000,
    "recording_content_hash": "a…", "recording_time_base_num": 1, "recording_time_base_den": 90000,
    "session_id": "1…", "electrical_clock_id": "3…",
    "electrical_time_base_num": 1, "electrical_time_base_den": 1000,
    "synchronization_method": "shared_hardware_event",
    "alignment_uncertainty_num": 1, "alignment_uncertainty_den": 1000,
    "measurement_source_sha256": "e…",
    "status": "incomplete"
  },
  "range": {"first_frame": 0, "frame_count": 2, "total_frame_count": 90000, "next_first_frame": 2},
  "frames": [
    {
      "output_frame_index": 0, "presentation_timestamp": 0, "presentation_time_seconds": 0.0,
      "recording_frame_index": 0, "recording_start_timestamp": 100, "recording_end_timestamp": 150,
      "status": "complete", "sample_start_index": 0, "sample_end_index": 5,
      "channels": [{"channel_id": "power", "quantity": "power", "unit": "W",
                    "mean": 2.0, "minimum": 0.0, "maximum": 4.0}]
    },
    {
      "output_frame_index": 1, "presentation_timestamp": 3600, "presentation_time_seconds": 0.04,
      "recording_frame_index": 2, "recording_start_timestamp": 150, "recording_end_timestamp": 180,
      "status": "sample_gap", "sample_start_index": 5, "sample_end_index": 7, "channels": []
    }
  ]
}
```

Non-complete frames carry their status and no values.

| Status | Code | Cause |
| --- | --- | --- |
| 400 | `invalid_request` | Missing hash, out-of-bounds or unknown parameter |
| 400 | `invalid_range` | `first_frame` at or beyond the last output frame |
| 400 | `unknown_channel` | Channel not in the series |
| 404 | `video_not_found`, `measurement_not_found` | |
| 409 | `stale_generation` | Requested generation is not the published one |
| 409 | `recording_mismatch` | Recording identity changed since import |
| 409 | `provenance_unresolved`, `provenance_unavailable`, `synchronization_missing` | Alignment not permitted |
| 409 | `alignment_rejected` | lx_dtypes `ElectricalAlignmentError` (for example uncertainty above the profile) |
| 409 | `source_integrity_failed` | Stored document hash or identity mismatch |

## Frontend

lx-annotate `VideoExaminationAnnotation` offers an opt-in lane below the segment
timeline (off by default, remembered per browser). It pages through the frames
endpoint for one channel, draws each contiguous run of complete frames as its
own line with a minimum/maximum band, shades non-complete frames by status and
never connects values across gaps. The lane always shows the full video at
zoom 1; it does not follow the segment timeline zoom.

## Activation-summary CSV and XLSX imports

`VideoElectrosurgicalActivationLog` (migration `0121_electrosurgical_activation_log`)
stores the original export through encrypted storage. Its immutable, validated
metadata binds the original hash, normalized contract hash, timezone, parser,
recording synchronization and operator-supplied timing tolerance. Reimporting the
same file with a different binding or tolerance is rejected. Protected electrical
rows are checked before full video deletion can remove any media files.

For the supplied Activations export, the operator confirmed that `t` marks the
activation start and the device uses the laptop's fixed UTC+01:00 clock. Use
`Etc/GMT-1` for that clock, not a region with daylight-saving transitions.
`P_avg_W` and `E_tot_J` remain **per-activation** average power and energy.
They are not converted into a sampled power curve or cumulative energy.

```text
python manage.py import_electrosurgical_activations --video-id VIDEO_ID \
  --source Activations.csv --source-format csv --source-timezone Etc/GMT-1 \
  --synchronization VERIFIED_SYNC_JSON --max-timing-error-seconds APPROVED_TOLERANCE
```

For XLSX use `--source-format xlsx`. Both formats require the ten-column export
including its unnamed index column. The bounded XLSX reader accepts one declared
worksheet with a matching internal relationship, preserves decimal cell lexemes,
and rejects formulas and external worksheet references. Excel fractional-day
timestamps are rounded to microseconds using half-even rounding. The recorded
conversion error does not estimate device accuracy or the spreadsheet exporter's
earlier rounding; CSV and XLSX exports are not assumed numerically identical.

The synchronization JSON uses `RecordingSynchronization`: recording PTS ticks on
the video side and UTC Unix microseconds on the electrical side
(`electrical_time_base_num=1`, `electrical_time_base_den=1000000`). Its recording
hash, session and clock identifiers must identify the actual recording. At least
two measured anchors and their uncertainty are required. Timestamp conversion
error plus synchronization uncertainty must fit the supplied tolerance. Examples
in tests are synthetic and must not be reused as real recording anchors.

The measurements overview includes `activation_logs` and
`max_activations_per_request=500`. Authenticated center members can request
`media/videos/<pk>/electrical-activations/<activation_log_id>/` with required
`generation_content_hash`, `first_activation` (default 0), and `activation_count`
(1–500). Each page carries file and generation identities, continuation bounds,
and `activation_frame_coverage_v1` results. Every read authenticates and verifies
the encrypted original and its normalized identity. Wrong keys, tampering and
identity mismatches fail closed without returning summaries.

Mapped intervals cover whole output-frame windows derived from verified ancestry;
the displayed frame boundaries are not estimates of sub-frame activation timing.
Zero-duration activations are point markers. Intervals outside synchronization or
recording bounds, or beyond the final known frame boundary, carry a status and
no summary values. No timing extrapolation is performed. The frontend displays
bounded pages of summaries, preserves decimal text and seeks only verified PTS.
An older backend without activation support is reported explicitly.

Run synthetic and optional supplied-export acceptance with:

```text
ACTIVATION_ACCEPTANCE_DIRECTORY=/path/to/supplied/exports RUN_VIDEO_TESTS=true \
  python -m pytest tests/services/test_electrosurgical_activation_import.py \
  tests/services/test_electrosurgical_activation_storage.py \
  tests/services/test_video_owned_deletion.py
```

The optional supplied-file tests expect the two 868-row Activations exports and
create synthetic video timelines and anchors in the isolated test database. They
verify encrypted persistence, preserved summary values and complete pagination,
not clinical alignment or device approval. Without the directory setting those
two tests are explicitly skipped. The normal suite covers wrong-key and tamper
rejection, rollback cleanup, protected-media deletion, API scope and generation
checks. Deployment requires the new backend migration and a reviewed release;
the installed 1.8.9 package does not contain this importer.

## Remaining release questions

- Device support matrix, approved aggregation profiles and clinically
  meaningful default channels (the lane defaults to `power`).
- An exact recording end in recording PTS ticks so the terminal frame can be
  bounded.
- Whether the import envelope and a host channel-descriptor contract belong in
  lx_dtypes.
- Video deletion workflows must remove measurement rows and their encrypted
  sources explicitly (`PROTECT` blocks deletion today).
- Per-request decryption and validation of the whole series limits
  throughput for long recordings; a persisted alignment inside the encrypted
  boundary would need its own identity-bound invalidation design.
- Alignment relies on the persisted `processed_video_hash`; it does not re-hash
  the processed file per request.
