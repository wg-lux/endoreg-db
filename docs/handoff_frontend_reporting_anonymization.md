# Frontend Handoff: Reporting + Anonymization Validation

## Scope
This handoff documents the backend contract needed by frontend flows that validate PDF anonymization and then continue in reporting views.

## Core Model Distinction (Important)
- `RawPdfFile`: pre-validation import artifact.
- `AnonymExaminationReport`: report record created/updated after successful anonymization validation.

Practical rule:
- Do not treat `RawPdfFile` as the final report object.
- Use `RawPdfFile` during anonymization workflow.
- Use `AnonymExaminationReport` (and reporting APIs) for finalized report-level behavior.

## API Changes You Should Use

### 1) Document type dropdown (for validation form)
- Endpoint: `GET /api/anonymization/document-types/dropdown/`
- Returns:

```json
[
  { "value": "report_draft", "label": "report_draft" },
  { "value": "report_final", "label": "report_final" },
  { "value": "pathology_draft", "label": "pathology_draft" },
  { "value": "pathology_final", "label": "pathology_final" },
  { "value": "pathology_addon", "label": "pathology_addon" }
]
```

Frontend requirement:
- Render this as a dropdown in PDF validation UI.

### 2) PDF anonymization validate now requires `document_type`
- Endpoint: `POST /api/anonymization/<file_id>/validate/`
- For PDF flow send `file_type: "pdf"` and `document_type`.

Example request body:

```json
{
  "file_type": "pdf",
  "document_type": "report_final",
  "patient_first_name": "Max",
  "patient_last_name": "Mustermann",
  "patient_dob": "21.03.1994",
  "examination_date": "15.02.2024",
  "casenumber": "12345",
  "anonymized_text": "..."
}
```

Error behavior:
- Missing `document_type` -> `400` with:
  - `error: "document_type is required for pdf validation."`
  - `allowed_document_types: [...]`
- Unsupported `document_type` -> `400` with allowed list.

Success response now includes report metadata:

```json
{
  "message": "report validated.",
  "timestamp": "2026-03-02T10:00:00+01:00",
  "report_file": {
    "id": 123,
    "document_type": "report_final",
    "created": true
  }
}
```

## Fetching by Patient Examination + Anonymized Text Display

### 3) Timeline supports `patient_examination_id` filtering
- Endpoint:
  - `GET /api/media/patients/<patient_id>/timeline/?patient_examination_id=<id>`
- If `patient_examination_id` is not an integer -> `400`.

Timeline payload additions:
- `full_report` items include:
  - `patient_examination_id`
  - `document_type` (`AnonymExaminationReport.type.name`, if set)
  - `anonymized_text` (from report text)
- `pdf` items include:
  - `patient_examination_id` (from raw pdf exam link or linked report)
  - `document_type` (from linked full report type, if available)
  - `anonymized_text` (resolved text)

### 4) PDF media detail/list resolve anonymized text robustly
- Endpoints:
  - `GET /api/media/pdfs/<id>/`
  - `GET /api/media/pdfs/`
- Text resolution order:
  1. `raw_pdf_file.anonymized_text`
  2. linked `anonym_examination_report.text`
  3. `sensitive_meta.anonymized_text`

List filter:
- `GET /api/media/pdfs/?patient_examination_id=<id>`

## Frontend Integration Checklist
- Add endpoint constant:
  - `anonymization/document-types/dropdown/`
- In PDF validation UI:
  - Load dropdown options on form init.
  - Make selection required before submit.
  - Send `document_type` with validation POST.
- On validate success:
  - Store/use `report_file.id` for navigation if needed.
- In timeline/reporting pages:
  - Pass `patient_examination_id` when context is exam-specific.
  - Prefer `anonymized_text` from response for display.
  - Use `document_type` for report labeling/badges where available.

## Suggested Minimal QA
1. Validate a PDF without `document_type` and confirm 400.
2. Validate a PDF with `document_type=report_final` and confirm `report_file.id` is returned.
3. Open patient timeline with `patient_examination_id` and confirm only exam-scoped items appear.
4. Confirm `document_type` appears for `full_report` (and linked `pdf` when present).
5. Confirm anonymized text renders from timeline and PDF detail response.
