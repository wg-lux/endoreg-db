# Video re-import processing budget

| Field | Value |
| --- | --- |
| `feature` | [Anonymization.yml](../feature-tracking/Anonymization.yml), `bounded_video_reimport` |
| `scope` | Celery video re-import dispatch; existing direct task calls retain their task defaults. |

## Budget

`VIDEO_REIMPORT_FRAMES_PER_SECOND` is processing throughput used to plan task
time, independent of source FPS and persisted presentation timestamps. The
implementation is `endoreg_db/services/jobs/video_reimport_budget.py`.

**formula:** ceil(frame_count / frames_per_second * margin + finalization_seconds)

### Inputs

#### `VIDEO_REIMPORT_FRAMES_PER_SECOND`

| Field | Value |
| --- | --- |
| `default` | 5.0 |
| `meaning` | Conservative planning estimate; replace with measured throughput for the selected worker and representative exhaustive workloads. |

#### `VIDEO_REIMPORT_TIME_MARGIN`

| Field | Value |
| --- | --- |
| `default` | 1.5 |
| `minimum` | 1.0 |

#### `VIDEO_REIMPORT_FINALIZATION_SECONDS`

| Field | Value |
| --- | --- |
| `default` | 3600 |
| `meaning` | Allowance for materialization, encoding, HLS generation and other non-OCR work; calibrate alongside throughput. |

#### `VIDEO_REIMPORT_MAX_SECONDS`

| Field | Value |
| --- | --- |
| `default` | 86400 |
| `maximum` | 86400 |
| `hard_limit` | soft_limit + 300 seconds of cleanup grace |
| `missing_frame_count` | Reject before enqueueing; restore verified source metadata through the existing metadata workflow. |
| `oversized_work` | Reject explicitly; never clamp the estimate or reduce OCR coverage. |

### Example

| Field | Value |
| --- | --- |
| `frames` | 105307 |
| `soft_seconds` | 35193 |
| `hard_seconds` | 35493 |
| `note` | Planning example using the default throughput and margin. |

## Failures

| Field | Value |
| --- | --- |
| `metadata` | Frame observations validate atomically. Gender tokens use word boundaries and the shared male/female literals; invalid observations propagate with redacted field-level validation codes. |
| `timeout` | Direct or causally wrapped Celery timeouts persist processing_timeout in processing history and upload-job error detail. |
| `retries` | No automatic timeout retry; review resource capacity and calibration before an authorized retry. |
| `stale_recovery` | Pending histories use a 25-hour-plus-cleanup bound. Running reimports are not reclaimed by creation age; reconcile abandoned running jobs only after verifying worker termination. |

## Telemetry

| Field | Value |
| --- | --- |
| `component` | lx-anonymizer FrameOCR |
| `event` | ocr.progress |
| `frequency` | First inference, at most once per minute during work, and inference failure. |

### Fields

- inference_calls_completed
- full_frame_calls_completed
- failures
- inference_seconds
- detection_seconds
- classification_seconds
- recognition_seconds
- stage_timing_calls
- session_providers

**Aggregation:** Per-instance totals count actual inference calls; exact-frame cache hits do not invoke optical character recognition (OCR). `session_providers` records configured sessions. Stage timings are recorded when supplied by the backend.

Verification commands, historical results and deployment follow-up belong to
[Anonymization.yml](../feature-tracking/Anonymization.yml), criterion
`bounded_video_reimport`.
