/**
 * Typed API endpoint contract for endoreg_db routes.
 *
 * Important:
 * - Paths are relative to axios `r()` helper (which prefixes `api/`).
 * - Keep trailing slashes exactly as defined in Django urls.
 */

export type Id = number | string
export type UUID = string

export const endpoints = {
  auth: {
    bootstrap: 'auth/bootstrap',
    publicHome: 'endoreg_db/',
    login: 'login/',
    loginCallback: 'login/callback/',
    conf: 'conf/'
  },

  router: {
    examinations: 'examinations/',
    findings: 'findings/',
    classifications: 'classifications/',
    patientFindings: 'patient-findings/',
    patientExaminations: 'patient-examinations/',
    patientExaminationReports: 'patient-examination-reports/'
  },

  patient: {
    patients: 'patients/',
    patientById: (id: Id) => `patients/${id}/`,
    patientPseudonym: (id: Id) => `patients/${id}/pseudonym/`,
    centers: 'centers/',
    genders: 'genders/',
    patientFindings: 'patient-findings/',
    checkPatientExaminationExists: (id: Id) => `check_pe_exist/${id}/`
  },

  examination: {
    examinationFindings: (examinationId: Id) => `examinations/${examinationId}/findings/`,
    findingClassifications: (findingId: Id) => `findings/${findingId}/classifications/`,
    classificationChoices: (classificationId: Id) =>
      `classifications/${classificationId}/choices/`,

    patientExaminationCreate: 'patient-examinations/create/',
    patientExaminationDetail: (id: Id) => `patient-examinations/${id}/`,
    patientExaminationList: 'patient-examinations/list/',
    patientExaminationClassifications: (examId: Id) =>
      `patient-examinations/${examId}/classifications/`,
    patientExaminationFindings: (examinationId: Id) =>
      `patient-examinations/${examinationId}/findings/`
  },

  report: {
    patientExaminationReports: 'patient-examination-reports/',
    patientExaminationReportById: (id: Id) => `patient-examination-reports/${id}/`,
    patientExaminationReportsByPatientExamination: (patientExaminationId: Id) =>
      `patient-examination-reports/?patient_examination_id=${patientExaminationId}`,
    saveReportSubmission: 'patient-examination-reports/save-submission/',
    segmentFrameSelectorBase: 'patient-examination-reports/segment-frame-selector/',
    segmentFrameSelector: (patientExaminationId: Id, reportId?: Id) =>
      reportId == null
        ? `patient-examination-reports/segment-frame-selector/?patient_examination_id=${patientExaminationId}`
        : `patient-examination-reports/segment-frame-selector/?patient_examination_id=${patientExaminationId}&report_id=${reportId}`,
    reportHistoryContext: (patientExaminationId: Id, limit?: number) =>
      limit == null
        ? `patient-examination-reports/history-context/?patient_examination_id=${patientExaminationId}`
        : `patient-examination-reports/history-context/?patient_examination_id=${patientExaminationId}&limit=${limit}`
  },

  upload: {
    upload: 'upload/',
    uploadStatus: (id: UUID) => `upload/${id}/status/`
  },

  requirements: {
    evaluateRequirements: 'evaluate-requirements/'
  },

  stats: {
    examinations: 'examinations/stats/',
    videoSegment: 'video-segment/stats/',
    videoSegments: 'video-segments/stats/',
    sensitiveMeta: 'video/sensitivemeta/stats/',
    general: 'stats/'
  },

  anonymization: {
    itemsOverview: 'anonymization/items/overview/',
    current: (fileId: Id) => `anonymization/${fileId}/current/`,
    start: (fileId: Id) => `anonymization/${fileId}/start/`,
    status: (fileId: Id) => `anonymization/${fileId}/status/`,
    validate: (fileId: Id) => `anonymization/${fileId}/validate/`,
    pollingInfo: 'anonymization/polling-info/',
    clearLocks: 'anonymization/clear-locks/',
    hasRaw: (fileId: Id) => `anonymization/${fileId}/has-raw/`
  },

  mediaManagement: {
    status: 'media-management/status/',
    cleanup: 'media-management/cleanup/',
    forceRemove: (fileId: Id) => `media-management/force-remove/${fileId}/`,
    resetStatus: (fileId: Id) => `media-management/reset-status/${fileId}/`
  },

  media: {
    patientTimeline: (patientId: Id) => `media/patients/${patientId}/timeline/`,
    sensitiveMediaId: (pk: Id, mediaType: string) => `media/sensitive-media-id/${pk}/${mediaType}/`,

    videos: 'media/videos/',
    videoDetail: (pk: Id) => `media/videos/${pk}/details/`,
    videoStream: (pk: Id) => `media/videos/${pk}/stream/`,
    videoFrameStream: (pk: Id, frameNumber: Id) => `media/videos/${pk}/frames/${frameNumber}/stream/`,
    videoReimport: (pk: Id) => `media/videos/${pk}/reimport/`,
    exportAnnotated: 'media/videos/export-annotated/',

    videoCorrection: (pk: Id) => `media/videos/video-correction/${pk}`,
    videoMetadata: (pk: Id) => `media/videos/${pk}/metadata/`,
    videoFps: (pk: Id) => `media/videos/${pk}/fps/`,
    videoApplyMask: (pk: Id) => `media/videos/${pk}/apply-mask/`,
    videoRemoveFrames: (pk: Id) => `media/videos/${pk}/remove-frames/`,
    videoLabelsList: 'media/videos/labels/list/',
    frameAnnotationsRandomTask: 'media/annotations/frames/random-task/',
    frameAnnotationsBulkUpsert: 'media/annotations/frames/bulk-upsert/',
    frameAnnotationsSkip: 'media/annotations/frames/skip/',

    segmentsCollection: 'media/videos/segments/',
    segmentsStats: 'media/videos/segments/stats/',
    videoSegments: (pk: Id) => `media/videos/${pk}/segments/`,
    videoSegmentDetail: (pk: Id, segmentId: Id) => `media/videos/${pk}/segments/${segmentId}/`,
    videoSegmentValidate: (pk: Id, segmentId: Id) =>
      `media/videos/${pk}/segments/${segmentId}/validate/`,
    videoSegmentsValidateBulk: (pk: Id) => `media/videos/${pk}/segments/validate-bulk/`,
    videoSegmentsValidationStatus: (pk: Id) =>
      `media/videos/${pk}/segments/validation-status/`,

    ensureSegmentAnnotationsForVideo: (pk: Id) =>
      `media/videos/${pk}/ensure-segment-annotations/`,
    ensureSegmentAnnotationsBulk: 'media/videos/ensure-segment-annotations/',
    // Redundant AI-derived frame annotations (separate track, does not overwrite manual_annotation)
    // Default backend information_source_name: "prediction_annotation"
    ensurePredictionSegmentAnnotationsForVideo: (pk: Id) =>
      `media/videos/${pk}/ensure-prediction-segment-annotations/`,
    ensurePredictionSegmentAnnotationsBulk:
      'media/videos/ensure-prediction-segment-annotations/',

    videoSensitiveMetadata: (pk: Id) => `media/videos/${pk}/sensitive-metadata/`,
    videoSensitiveMetadataVerify: (pk: Id) => `media/videos/${pk}/sensitive-metadata/verify/`,

    pdfSensitiveMetadata: (pk: Id) => `media/pdfs/${pk}/sensitive-metadata/`,
    pdfSensitiveMetadataVerify: (pk: Id) => `media/pdfs/${pk}/sensitive-metadata/verify/`,
    sensitiveMetadataList: 'media/sensitive-metadata/',
    pdfSensitiveMetadataList: 'media/pdfs/sensitive-metadata/',

    pdfs: 'media/pdfs/',
    pdfDetail: (pk: Id) => `media/pdfs/${pk}/`,
    // Inline view by default. Add query params manually for mode control:
    // ?type=raw|processed&download=1 (download forces attachment)
    pdfStream: (pk: Id) => `media/pdfs/${pk}/stream/`,
    pdfExportProcessed: (pk: Id) => `media/pdfs/${pk}/stream/?type=processed&download=1`,
    pdfExportRaw: (pk: Id) => `media/pdfs/${pk}/stream/?type=raw&download=1`,
    pdfReimport: (pk: Id) => `media/pdfs/${pk}/reimport/`
  }
} as const

export type ApiEndpoints = typeof endpoints

## Patient Timeline Reporting Payload

`GET /api/media/patients/{patient_id}/timeline/` supports:

- `patient_examination_id=<int>`: scope timeline items to one examination.
- `latest_only=true`: compact payload for reporting pages.

When `latest_only=true`, backend returns:

```json
{
  "patient": {
    "id": 123,
    "first_name": "...",
    "last_name": "...",
    "dob": "...",
    "is_real_person": false,
    "patient_hash": "..."
  },
  "latest_report": {
    "media_type": "pdf|full_report",
    "id": 1,
    "anonymized_text": "...",
    "stream_options": [
      { "type": "raw", "url": "/api/media/pdfs/1/stream/?type=raw" },
      { "type": "processed", "url": "/api/media/pdfs/1/stream/?type=processed" }
    ]
  },
  "latest_video": {
    "media_type": "video",
    "id": 99,
    "stream_options": [
      { "type": "raw", "url": "/api/media/videos/99/stream/?type=raw" },
      { "type": "processed", "url": "/api/media/videos/99/stream/?type=processed" }
    ]
  },
  "latest_frames": [
    {
      "video_id": 99,
      "frame_number": 120,
      "category": "polyp|intervention|other_findings|fallback_latest",
      "selection_source": "segment_priority|latest_frame",
      "stream_url": "/api/media/videos/99/frames/120/stream/"
    }
  ]
}
```

Frame selection priority in `latest_only`:
1. `polyp`
2. `intervention`
3. `other_findings`
4. fallback to newest frames if fewer than 3 categorized frames are available.

## Case Generator and Export Availability

- Case generator:
  - currently script-based (`scripts/case_generator/prototype.py`)
  - no public REST endpoint exposed yet for frontend use.

- Report export:
  - use `media.pdfExportProcessed(pk)` (preferred) or `media.pdfExportRaw(pk)` for forced file download.

- Annotation/media export:
  - `POST /api/media/videos/export-annotated/` via `media.exportAnnotated`.
