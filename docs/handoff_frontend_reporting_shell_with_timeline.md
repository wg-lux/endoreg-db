# Frontend Handoff: Reporting Shell with Combined Patient Media Timeline

## Goal
Implement the reporting shell so it can load and display:
- latest report text (anonymized),
- latest video (with raw/processed stream options),
- latest three frames,
from a single backend call scoped to patient (and optionally patient examination).

Primary endpoint:
- `GET /api/media/patients/<patient_id>/timeline/?latest_only=true`
- Optional exam scoping: `&patient_examination_id=<id>`

This endpoint is implemented by `PatientMediaTimelineView` and is the canonical combined media link for reporting preload.

## Breaking Changes Notes (Frontend)
As of 2026-03-10, account for the following:

1. PDF validation now requires `document_type`.
   - Endpoint: `POST /api/anonymization/<pdf_id>/validate/`
   - If omitted, backend can return `400`.
   - Reporting flow should send `document_type: "report_final"` for final report PDFs.

2. `timeline` response shape differs when `latest_only=true`.
   - With `latest_only=true`, response is a compact object:
     - `patient`
     - `latest_report`
     - `latest_video`
     - `latest_frames`
   - It is not the full timeline list payload shape, so older list-based parsers must branch on this mode.

3. Stream URL handling is canonical-URL first.
   - Do not reconstruct stream/file paths in frontend.
   - Always use backend-provided `stream_options[].url` links.
   - This avoids storage-path mismatches between environments (dev vs nginx/proxy production).

## Where This Fits in Your Shell
Given your route structure:
- `/reporting`
- `/reporting/:patient_examination_id/template-requirements`
- `/reporting/:patient_examination_id/findings`
- `/reporting/:patient_examination_id/requirements-review`
- `/reporting/:patient_examination_id/report-editor`
- `/reporting/:patient_examination_id/frame-selector`
- `/reporting/:patient_examination_id/finalized`

recommended behavior:
1. Resolve `patient_id` from your flow context (case setup / lookup token payload / selected PE).
2. On shell init (or when `patient_examination_id` changes), fetch timeline `latest_only=true`.
3. Store result centrally (`reportingFlowStore`) so child routes reuse the same preload data.

## Endpoint Contract

### Request
- Path: `/api/media/patients/<patient_id>/timeline/`
- Query:
  - `latest_only=true` (required for compact reporting payload)
  - `patient_examination_id=<int>` (optional, recommended in exam-scoped workflow)

Example:
`/api/media/patients/42/timeline/?patient_examination_id=314&latest_only=true`

### Success response (`200`)
```json
{
  "patient": {
    "id": 42,
    "first_name": "Pseudo",
    "last_name": "Patient",
    "dob": "1990-01-01",
    "is_real_person": false,
    "patient_hash": "..."
  },
  "latest_report": {
    "media_type": "pdf",
    "id": 1201,
    "patient_examination_id": 314,
    "anonymized_text": "ANONYMIZED REPORT TEXT ...",
    "document_type": "report_final",
    "stream_options": [
      { "type": "raw", "url": "https://.../api/media/pdfs/1201/stream/?type=raw" },
      { "type": "processed", "url": "https://.../api/media/pdfs/1201/stream/?type=processed" }
    ]
  },
  "latest_video": {
    "media_type": "video",
    "id": 777,
    "patient_examination_id": 314,
    "stream_options": [
      { "type": "raw", "url": "https://.../api/media/videos/777/stream/?type=raw" },
      { "type": "processed", "url": "https://.../api/media/videos/777/stream/?type=processed" }
    ]
  },
  "latest_frames": [
    {
      "video_id": 777,
      "frame_number": 304,
      "category": "polyp",
      "selection_source": "segment_priority",
      "segment_id": 991,
      "segment_label": "polyp_x",
      "stream_url": "https://.../api/media/videos/777/frames/304/stream/"
    }
  ]
}
```

### Error responses
- `404`: patient not found.
- `400`: `patient_examination_id` is present but not an integer.
- `403`: permission/policy denied in non-debug environments.

## Selection Rules You Can Rely On
- `latest_report`: newest item among `media_type in {"pdf", "full_report"}`.
- `latest_video`: newest `media_type == "video"`.
- `latest_frames` (max 3):
  1. one from `polyp` category (if available),
  2. one from `intervention` category (if available),
  3. one from `other_findings` category (if available),
  4. remaining slots filled by newest frame rows (`fallback_latest`).

This already supports your requirement for prioritized categories:
- polyp
- intervention
- other findings mapped to segments

## Multi-stream Handling (PDF + Video)
Do not construct stream URLs manually; use `stream_options` from API response.

Recommended client helper:
- prefer `processed` when present,
- fallback to `raw`.

```ts
export function pickPreferredStream(
  options: Array<{ type: string; url: string }> = []
): string | null {
  return (
    options.find((o) => o.type === 'processed')?.url ??
    options.find((o) => o.type === 'raw')?.url ??
    null
  )
}
```

## Suggested Store Shape (Vue/Pinia)
```ts
type TimelineLatestPayload = {
  patient: {
    id: number
    first_name: string | null
    last_name: string | null
    dob: string | null
    is_real_person: boolean
    patient_hash: string | null
  }
  latest_report: null | Record<string, unknown>
  latest_video: null | Record<string, unknown>
  latest_frames: Array<Record<string, unknown>>
}
```

Recommended state:
- `reporting_context.patient_id`
- `reporting_context.patient_examination_id`
- `media_preload: TimelineLatestPayload | null`
- `media_preload_status: 'idle' | 'loading' | 'ready' | 'error'`
- `media_preload_error: string | null`

## Integration Steps
1. Add API accessor:
   - `media/patients/${patient_id}/timeline/?latest_only=true&patient_examination_id=${pe_id}`
2. Trigger fetch:
   - on shell mount when `patient_id` available,
   - and whenever `patient_examination_id` changes.
3. In `report-editor`:
   - preload editor text from `latest_report.anonymized_text` if current draft is empty.
4. In `frame-selector`:
   - preload from `latest_frames`.
5. In findings/review pages:
   - show quick preview cards using `latest_video` and `latest_report`.

## UX/Resilience Guidelines
- Render page even if one media block is missing (`latest_report` or `latest_video` may be `null`).
- Treat `latest_frames` as optional list; empty is valid.
- Show explicit fallback states:
  - no report available,
  - no video available,
  - no frames available.
- Keep request idempotent and re-callable (refresh button in shell is useful).

## Sensitive Metadata Clarification
This timeline latest payload is designed for reporting consumption and includes anonymized report text and stream links.
It does not expose full sensitive metadata object by default.

If UI needs editable sensitive fields, use dedicated endpoints:
- video sensitive metadata: `/api/media/videos/<pk>/sensitive-metadata/`
- pdf sensitive metadata: `/api/media/pdfs/<pk>/sensitive-metadata/`

## Frontend QA Checklist
1. Load shell with valid `patient_id` + `patient_examination_id`; confirm one API call returns report/video/frames preload.
2. Confirm video and PDF links open/stream using provided `stream_options`.
3. Confirm frame cards show prioritized categories when segment mappings exist.
4. Confirm fallback to newest frames when no categorized segments exist.
5. Confirm report editor initializes from `latest_report.anonymized_text`.
6. Confirm `404/400/403` are surfaced with actionable UI messages.
7. Confirm navigation across shell subroutes reuses cached preload state (no unnecessary refetch loops).


### Report Export (PDF)
- Use report stream endpoint and set download mode:
  - `GET /api/media/pdfs/<pdf_id>/stream/?type=processed&download=1`
  - fallback raw: `GET /api/media/pdfs/<pdf_id>/stream/?type=raw&download=1`
- For inline preview, omit `download=1`.
- In reporting shell, use `latest_report.stream_options` from timeline payload and append `download=1` when user clicks export/download.

### Video/Frame Annotation Export
- Existing endpoint:
  - `POST /api/media/videos/export-annotated/`
- Purpose: backend export job for labeled annotations/media artifacts (CSV/JSON + optional media copies), not direct report-PDF export.
- Typical payload (example):
```json
{
  "output_path": "data/export/frames.csv",
  "output_format": "csv",
  "video_id": 777,
  "export_videos": true,
  "export_frames": true,
  "use_export_flags": true
}
```
- Response includes:
  - `success`
  - `output_path`
  - `row_count`
  - `exported_video_count`
  - `exported_frame_count`

## Rust PDF Renderer State

Rust module path:
- `tools/report_pdf_renderer_rust/src/main.rs`

Current backend integration state:
- integrated and used by report persistence during report submission save.
- backend entry points:
  - `endoreg_db/services/report_pdf_renderer.py`
  - `endoreg_db/services/report_persistence.py`
- report submission endpoint that triggers persistence flow:
  - `POST /api/patient-examination-reports/save-submission/`

Runtime behavior:
- backend tries Rust renderer binary first:
  - resolved via `ENDOREG_REPORT_PDF_RENDERER_BIN`, else `report_pdf_renderer` from `PATH`.
- if Rust renderer is missing/fails/times out, backend falls back to internal minimal PDF generation.
- frontend does not need separate logic for renderer choice; consume returned artifact URLs as usual.

Frontend implication:
- treat PDF artifact generation as eventually consistent backend behavior.
- always read `persisted_artifacts` from save-submission response and then use media stream endpoints.
- do not assume Rust-specific features in UI; output availability is stable because of fallback path.
