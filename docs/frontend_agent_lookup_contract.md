# Lookup Contract Guide For Frontend Agents

## Purpose
This guide defines how frontend agents should interact with lookup endpoints and how to surface unfulfilled requirements to users.

Primary goals:
- Keep request payloads stable and typed.
- Use snake_case keys.
- Render requirement failures and `suggested_actions` consistently.
- Treat requirement evaluation as advisory guidance (nag), not a hard protocol lock.

## Canonical Contract Source
Lookup contracts are defined in `lx_dtypes`:
- `lx_dtypes.models.knowledge_base.report_template.LookupState`
- `lx_dtypes.models.knowledge_base.report_template.LookupStateDataDict`

`endoreg_db` imports these contracts through:
- `endoreg_db/schemas/lookup_state.py`

Treat `lx_dtypes` as the source of truth.

Related backend integrations implemented in this thread:
- `lx_dtypes` report-template schema now supports first-class non-finding sections (`patient_data`, `history`) with typed `fields`.
- `endoreg_db` now persists report editor submissions to `PatientExaminationReport`.
- `endoreg_db` derives patient history context from prior `PatientExamination` / `PatientFinding` records.
- `endoreg_db` requirement priors now consume history-derived tokens (read-only).
- `endoreg_db` report persistence evaluates requirements after save and returns advisory guidance (non-blocking).

Prior source order for `candidate_requirement_set_ids`:
1. Typed report-template directed graph contracts from `lx_dtypes` (authoritative).

If no valid graph prior is available, low-confidence/no-prior behavior is expected and backend evaluates all sets.

## Endpoints
Base viewset: `endoreg_db/views/requirement/lookup.py`

1. `POST /lookup/init/`
- Request: `patient_examination_id`, optional `user_tags`
- Response: `{ "token": "<token>" }` (HTTP `201 Created`)

2. `GET /lookup/{token}/all/`
- Response: full typed lookup state.

3. `GET /lookup/{token}/parts/?keys=...`
- Response: typed subset of requested keys.

4. `PATCH /lookup/{token}/parts/`
- Request:
```json
{
  "updates": {
    "selected_requirement_set_ids": [1, 2],
    "selected_choices": {
      "10": { "choice_id": 3 }
    }
  }
}
```
- Response: `{ "ok": true, "token": "<token>" }`

5. `POST /lookup/{token}/recompute/`
- Response:
```json
{
  "ok": true,
  "token": "abc",
  "updates": {
    "requirements_by_set": {},
    "requirement_status": {},
    "requirement_set_status": {},
    "requirement_defaults": {},
    "classification_choices": {},
    "suggested_actions": {}
  }
}
```

## New Report Persistence Endpoints (Thread Additions)
Base viewset: `endoreg_db/views/report/patient_examination_report.py`

1. `POST /patient-examination-reports/save-submission/`
- Persists report payload + normalized findings/indications/patient context.
- Evaluates requirements after persistence (advisory only).
- Accepts optional `selected_requirement_set_ids` to scope requirement guidance.

Example request:
```json
{
  "patient_examination_id": 123,
  "template_name": "star_upper_gi_main",
  "status": "final",
  "editor_payload": {},
  "rendered_text": "Narrative report text...",
  "patient_data": {
    "patient_birth_date": "1975-03-14",
    "patient_gender": "male"
  },
  "indications": [
    {
      "examination_indication_id": 1,
      "indication_choice_id": 2
    }
  ],
  "findings": [
    {
      "finding": "gastroscopy_polyp",
      "classifications": [
        {
          "classification": "lesion_size_mm",
          "classification_choice": 28
        }
      ],
      "interventions": []
    }
  ],
  "selected_requirement_set_ids": [10, 11],
  "expected_version": 2
}
```

Example response (abridged):
```json
{
  "report": { "id": 55, "status": "final", "version": 3 },
  "created": false,
  "warnings": [
    "Requirement guidance: 2 requirement(s) are currently unmet.",
    "Final report saved with guideline deviations. This is advisory-only and does not block clinician workflow."
  ],
  "history_context": { "previous_examinations": [] },
  "requirement_guidance": {
    "advisory_only": true,
    "requirement_status": { "1": false, "2": true },
    "requirement_set_status": { "10": false },
    "suggested_actions": { "1": [{ "type": "add_finding", "finding_id": 7 }] },
    "candidate_requirement_set_ids": [10, 11],
    "candidate_requirement_set_confidence": 0.62
  },
  "persisted_report_artifact_id": 71,
  "persisted_pdf_artifact_id": 412,
  "persisted_artifacts": {
    "full_report_id": 71,
    "pdf_id": 412,
    "pdf_view_url": "https://<host>/api/media/pdfs/412/stream/?type=raw",
    "pdf_download_url": "https://<host>/api/media/pdfs/412/stream/?type=raw&download=1",
    "patient_timeline_url": "https://<host>/api/media/patients/88/timeline/"
  }
}
```

2. `GET /patient-examination-reports/history-context/?patient_examination_id=<id>&limit=5`
- Returns history-derived context built from prior examinations/findings.
- Read-only helper for UI/report composition.

3. `GET|PATCH /patient-examination-reports/segment-frame-selector/?patient_examination_id=<id>[&report_id=<id>]`
- Builds the report frame-selection page state from `LabelVideoSegment` + `Frame`.
- Persists one optional selected frame per segment in `PatientExaminationReport.editor_payload`.
- Also supports attaching a `Finding` (stored/reused through `PatientFinding` + `LabelVideoSegment.patient_findings`).
- `GET` reads `patient_examination_id` / optional `report_id` from query params.
- `PATCH` requires `patient_examination_id` in the JSON body (and accepts optional `report_id` / `template_name`).

GET response highlights:
- `report_id` (auto-created draft report if none existed)
- `storage_key` = `report_segment_frame_selections`
- `results[]` with:
  - `segment_id`
  - `start_frame_number`, `end_frame_number`
  - `selected_frame_number`
  - `selected_frame` (includes `frame_id`, `relative_path`, `timestamp`)
  - `controls.random_frame_number`
  - `controls.step_backward_5_frame_number`
  - `controls.step_forward_5_frame_number`
  - `attached_finding` (preselected next time for this segment/examination)

PATCH body (snake_case) examples:
```json
{
  "patient_examination_id": 123,
  "report_id": 55,
  "segment_id": 9001,
  "action": "random"
}
```

```json
{
  "patient_examination_id": 123,
  "report_id": 55,
  "segment_id": 9001,
  "action": "step",
  "step": 5
}
```

```json
{
  "patient_examination_id": 123,
  "report_id": 55,
  "segment_id": 9001,
  "action": "set",
  "frame_number": 1820,
  "finding_id": 7
}
```

```json
{
  "patient_examination_id": 123,
  "report_id": 55,
  "segment_id": 9001,
  "action": "clear"
}
```

## Production PDF Workflow (Thread Addition)
When `save-submission` is called with `status: "final"`, the backend now attempts to:
- persist a full report artifact (`AnonymExaminationReport`)
- persist a PDF media artifact (`RawPdfFile`)
- return browser-usable URLs for preview/download

Response fields for frontend integration:
- `persisted_report_artifact_id`
- `persisted_pdf_artifact_id`
- `persisted_artifacts.full_report_id`
- `persisted_artifacts.pdf_id`
- `persisted_artifacts.pdf_view_url`
- `persisted_artifacts.pdf_download_url`
- `persisted_artifacts.patient_timeline_url`

Recommended frontend flow after final save:
1. Call `POST /patient-examination-reports/save-submission/` with `status: "final"`.
2. If `persisted_artifacts.pdf_download_url` exists:
1. Trigger browser download via link navigation (`window.open` or hidden `<a>` click).
3. Optionally open `persisted_artifacts.pdf_view_url` in a new tab for preview.
4. Refresh `persisted_artifacts.patient_timeline_url` to show generated `full_report` / `pdf` items.
5. If artifact fields are missing, treat as non-blocking and surface `warnings`.

PDF stream endpoint behavior:
- `GET /api/media/pdfs/{id}/stream/` defaults to inline preview
- add `?download=1` to force browser attachment download
- `type` query param supported: `raw` (default) or `processed`

Patient timeline endpoint (media integration):
- `GET /api/media/patients/{patient_id}/timeline/`
- Returns combined `full_report`, `pdf`, `video` items ordered by normalized timestamp
- Each item includes:
- `timestamp`
- `timestamp_source`
- `timestamp_is_examination_date`
- `linked_patient`
- `pseudo_patient`
- `patient_link_sources`
- Frontend should support both real and pseudo patients via `is_real_person`

## Key Naming Rules
- Always send snake_case keys.
- Do not send camelCase from frontend agents.

Use:
- `selected_requirement_set_ids`
- `selected_choices`

Avoid:
- `selectedRequirementSetIds`
- `selectedChoices`

## Rendering Unfulfilled Requirements
Use these fields:
- `requirement_status`: map of requirement id -> bool
- `requirement_set_status`: map of requirement set id -> bool
- `suggested_actions`: map of requirement id -> list of action objects
- `candidate_requirement_set_ids`: Markov-prior candidate set IDs
- `candidate_requirement_set_confidence`: confidence score (0.0..1.0)

For report persistence responses (`save-submission`), read the same fields from:
- `requirement_guidance.requirement_status`
- `requirement_guidance.requirement_set_status`
- `requirement_guidance.suggested_actions`
- `requirement_guidance.candidate_requirement_set_ids`
- `requirement_guidance.candidate_requirement_set_confidence`

Recommended UX flow:
1. Show failed sets where `requirement_set_status[set_id] == false`.
1. Use `candidate_requirement_set_ids` only as ranking/scope hints.
1. If `candidate_requirement_set_confidence < 0.35`, treat candidate hints as low confidence.
2. Expand failed requirements where `requirement_status[req_id] == false`.
3. Show suggested actions directly under each failed requirement.
4. Allow one-click action application where possible (for example `add_finding`).
5. Recompute after local change proposals are applied to server state.

## Advisory-Only Requirement UX (Important)
Requirement evaluation in report persistence is intentionally non-blocking.

Interpretation:
- The backend warns when saved content deviates from guideline-driven requirement expectations.
- The backend does **not** block clinicians from saving or finalizing reports solely due to unmet requirements.
- Frontend should present deviations as guidance, not as hard validation errors.

Recommended UI language:
- "Guideline deviation detected"
- "Requirement not met (advisory)"
- "You may proceed if clinically justified"

Do not present as:
- "Save failed"
- "Submission invalid"
- "Protocol violation (blocked)"

## Suggested Action Handling
Current action patterns include:
- `add_finding`
- `edit_patient`

Frontend agent behavior:
- Handle unknown action types defensively.
- Render unknown actions as generic suggestions, not hard failures.
- Log unrecognized action type for telemetry.

## History-Aware Priors (Thread Additions)
Requirement-set prior ranking now uses:
- Current examination name
- Current patient finding names
- History-derived tokens from prior examinations/findings (best-effort)

Notes:
- Priors remain assistive only.
- If history lookup fails, backend falls back to non-history prior behavior.
- `markov_prior_service` remains stateless/read-only (no persistence).

## Error Handling
- `404` on expired/missing token: call `init` again, then resume with new token.
- `400` on invalid payload: validate request keys and value types before retry.
- Keep retry logic idempotent for `recompute`.
- For report save:
- `400` may indicate data-integrity issues (unknown finding/classification/intervention, version conflict), not guideline deviations.
- Guideline deviations are returned in `warnings` / `requirement_guidance`, not as transport errors.

## Agent Advice
- Keep a local typed state mirror matching the lookup contract.
- Treat backend `updates` as partial patches, then merge into local state.
- Never infer field names; use the contract constants/types.
- Prefer strict schema checks at API boundary before mutating UI state.

## Thread Summary (Implemented)
- `lx_dtypes` report-template schema extension:
- `ReportTemplateSection.section_kind` supports `findings | patient_data | history`
- `ReportTemplateSection.fields` supports typed field definitions
- Graph/validator support added for patient/history nodes and field checks
- `endoreg_db` report persistence:
- Added `PatientExaminationReport` model for persisted report artifact + snapshots
- Added transactional `save_report_submission(...)`
- Added report history builder from prior DB records
- Added DRF endpoint `/api/patient-examination-reports/save-submission/`
- Added DRF endpoint `/api/patient-examination-reports/history-context/`
- Requirement integration:
- Added history-aware priors to `markov_prior_service`
- Added advisory requirement evaluation helper in `lookup_service`
- Wired report save endpoint to return non-blocking requirement guidance
- Startup/migration integration:
- Added early `lx-data-models` path bootstrap in `endoreg_db/urls/__init__.py`
- Migration for `PatientExaminationReport` was generated and trimmed to avoid unrelated destructive operations
