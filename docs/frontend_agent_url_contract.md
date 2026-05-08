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
  root: {
    apiRoot: ''
  },

  auth: {
    bootstrap: 'auth/bootstrap',
    publicHome: 'endoreg_db/',
    login: 'login/',
    loginCallback: 'login/callback/',
    conf: 'conf/'
  },

  router: {
    examinations: 'examinations/',
    examinationById: (id: Id) => `examinations/${id}/`,
    examinationFindings: (id: Id) => `examinations/${id}/findings/`,

    findings: 'findings/',
    findingById: (id: Id) => `findings/${id}/`,
    findingLookupById: (findingId: Id) => `findings/by-id/${findingId}/`,
    findingLocationClassifications: (id: Id) =>
      `findings/${id}/location_classifications/`,
    findingMorphologyClassifications: (id: Id) =>
      `findings/${id}/morphology_classifications/`,

    classifications: 'classifications/',
    classificationById: (id: Id) => `classifications/${id}/`,
    classificationChoices: (id: Id) => `classifications/${id}/choices/`,

    patientFindings: 'patient-findings/',
    patientFindingById: (id: Id) => `patient-findings/${id}/`,
    patientFindingLookupById: (patientFindingId: Id) =>
      `patient-findings/by-id/${patientFindingId}/`,
    patientFindingsByExamination: (patientExaminationId: Id) =>
      `patient-findings/by-examination/${patientExaminationId}/`,

    patientExaminations: 'patient-examinations/',
    patientExaminationById: (id: Id) => `patient-examinations/${id}/`,
    patientExaminationPatientsDropdown: 'patient-examinations/patients_dropdown/',
    patientExaminationExaminationsDropdown:
      'patient-examinations/examinations_dropdown/',
    patientExaminationRecent: 'patient-examinations/recent/',
    patientExaminationDetails: (id: Id) => `patient-examinations/${id}/details/`,
    patientExaminationDraft: (id: Id) => `patient-examinations/${id}/draft/`,

    patientExaminationReports: 'patient-examination-reports/',
    patientExaminationReportById: (id: Id) => `patient-examination-reports/${id}/`,
    patientExaminationReportHistoryContext:
      'patient-examination-reports/history-context/',
    patientExaminationReportSaveSubmission:
      'patient-examination-reports/save-submission/',
    patientExaminationReportSegmentFrameSelector:
      'patient-examination-reports/segment-frame-selector/'
  },

  patient: {
    patients: 'patients/',
    patientById: (id: Id) => `patients/${id}/`,
    patientCount: 'patients/patient_count/',
    patientCheckDeletionSafety: (id: Id) => `patients/${id}/check_deletion_safety/`,
    patientPseudonym: (id: Id) => `patients/${id}/pseudonym/`,

    centers: 'centers/',
    centerById: (id: Id) => `centers/${id}/`,
    genders: 'genders/',
    genderById: (id: Id) => `genders/${id}/`,

    patientFindings: 'patient-findings/',
    patientFindingById: (id: Id) => `patient-findings/${id}/`,
    patientFindingLookupById: (patientFindingId: Id) =>
      `patient-findings/by-id/${patientFindingId}/`,
    patientFindingsByExamination: (patientExaminationId: Id) =>
      `patient-findings/by-examination/${patientExaminationId}/`,

    checkPatientExaminationExists: (id: Id) => `check_pe_exist/${id}/`
  },

  examination: {
    examinations: 'examinations/',
    examinationById: (id: Id) => `examinations/${id}/`,
    examinationFindings: (examinationId: Id) => `examinations/${examinationId}/findings/`,
    examinationIndications: (examId: Id) => `examinations/${examId}/indications/`,
    examinationInterventions: (examId: Id) => `examinations/${examId}/interventions/`,
    examinationFindingInterventions: (examId: Id, findingId: Id) =>
      `examinations/${examId}/findings/${findingId}/interventions/`,

    findings: 'findings/',
    findingById: (id: Id) => `findings/${id}/`,
    findingLookupById: (findingId: Id) => `findings/by-id/${findingId}/`,
    findingClassifications: (findingId: Id) => `findings/${findingId}/classifications/`,
    findingLocationClassifications: (id: Id) =>
      `findings/${id}/location_classifications/`,
    findingMorphologyClassifications: (id: Id) =>
      `findings/${id}/morphology_classifications/`,

    classifications: 'classifications/',
    classificationById: (id: Id) => `classifications/${id}/`,
    classificationChoices: (classificationId: Id) =>
      `classifications/${classificationId}/choices/`,
    indicationChoices: (indicationId: Id) => `indications/${indicationId}/choices/`,

    patientExaminations: 'patient-examinations/',
    patientExaminationCreate: 'patient-examinations/create/',
    patientExaminationDetail: (id: Id) => `patient-examinations/${id}/`,
    patientExaminationList: 'patient-examinations/list/',
    patientExaminationPatientsDropdown: 'patient-examinations/patients_dropdown/',
    patientExaminationExaminationsDropdown:
      'patient-examinations/examinations_dropdown/',
    patientExaminationRecent: 'patient-examinations/recent/',
    patientExaminationDetails: (id: Id) => `patient-examinations/${id}/details/`,
    patientExaminationDraft: (id: Id) => `patient-examinations/${id}/draft/`,
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

  stats: {
    examinations: 'examinations/stats/',
    videoSegment: 'video-segment/stats/',
    videoSegments: 'video-segments/stats/',
    sensitiveMeta: 'video/sensitivemeta/stats/',
    general: 'stats/',
    auditLedgerIntegrity: 'audit-ledger/integrity/'
  },

  anonymization: {
    itemsOverview: 'anonymization/items/overview/',
    current: (fileId: Id) => `anonymization/${fileId}/current/`,
    start: (fileId: Id) => `anonymization/${fileId}/start/`,
    status: (fileId: Id) => `anonymization/${fileId}/status/`,
    validate: (fileId: Id) => `anonymization/${fileId}/validate/`,
    documentTypesDropdown: 'anonymization/document-types/dropdown/',
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
    hubTransfers: 'media/hub/transfers/',
    hubTransferStatus: (transferKey: Id) => `media/hub/transfers/${transferKey}/status/`,
    hubTransferMedia: (transferKey: Id) => `media/hub/transfers/${transferKey}/media/`,

    patientTimeline: (patientId: Id) => `media/patients/${patientId}/timeline/`,
    sensitiveMediaId: (pk: Id, mediaType: string) => `media/sensitive-media-id/${pk}/${mediaType}/`,

    videos: 'media/videos/',
    videoDetailStream: (pk: Id) => `media/videos/${pk}/`,
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
    videoLabelSetsList: 'media/videos/label-sets/list/',
    videoPredictionModelsList: 'media/videos/prediction-models/list/',
    videoSegmentsRerunPredictions: (pk: Id) =>
      `media/videos/${pk}/segments/rerun-predictions/`,
    frameAnnotationsRandomTask: 'media/annotations/frames/random-task/',
    frameAnnotationsBulkUpsert: 'media/annotations/frames/bulk-upsert/',
    frameAnnotationsSkip: 'media/annotations/frames/skip/',

    segmentsStats: 'media/videos/segments/stats/',
    videoSegments: (pk: Id) => `media/videos/${pk}/segments/`,
    videoSegmentsBlackenOutside: (pk: Id) => `media/videos/${pk}/segments/blacken-outside/`,
    videoSegmentsBulk: (pk: Id) => `media/videos/${pk}/segments/bulk/`,
    videoSegmentsImportPredictions: (pk: Id) =>
      `media/videos/${pk}/segments/import-predictions/`,
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
    videoCaseResolution: (pk: Id) => `media/videos/${pk}/case-resolution/`,
    videoSensitiveMetadataVerify: (pk: Id) => `media/videos/${pk}/sensitive-metadata/verify/`,

    pdfSensitiveMetadata: (pk: Id) => `media/pdfs/${pk}/sensitive-metadata/`,
    pdfCaseResolution: (pk: Id) => `media/pdfs/${pk}/case-resolution/`,
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
    pdfReimport: (pk: Id) => `media/pdfs/${pk}/reimport/`,
    pdfApplyRedactions: (pk: Id) => `media/pdfs/${pk}/apply-redactions/`,
    pdfProcessingHistory: (pk: Id) => `media/pdfs/${pk}/processing-history/`
  },

  settings: {
    application: 'settings/application/',
    aiDatasetExport: 'settings/application/ai_dataset_export/',
    backup: 'settings/application/backup/',
    centersDropdown: 'settings/application/dropdowns/centers/',
    processorsDropdown: 'settings/application/dropdowns/processors/',
    annotatorsDropdown: 'settings/application/dropdowns/annotators/',
    reportTemplatesDropdown: 'settings/application/dropdowns/report_templates/',
    aiDatasetsDropdown: 'settings/application/dropdowns/ai_datasets/',
    networkNodeRolesDropdown:
      'settings/application/dropdowns/network_node_roles/',
    modelTrainingOptions: 'settings/application/model_training/options/',
    modelTrainingRuns: 'settings/application/model_training/runs/',
    modelTrainingRunDetail: (runId: Id) =>
      `settings/application/model_training/runs/${runId}/`,
    videoDimensionBackfillRuns:
      'settings/application/video_dimension_backfill/runs/',
    videoDimensionBackfillRunDetail: (runId: Id) =>
      `settings/application/video_dimension_backfill/runs/${runId}/`,
    networkNodes: 'settings/application/network_nodes/',
    networkNodeById: (pk: Id) => `settings/application/network_nodes/${pk}/`
  }
} as const

export type ApiEndpoints = typeof endpoints

## Complete Backend Route Coverage

This table is checked by `tests/docs/test_frontend_agent_url_contract.py`.
It intentionally omits DRF format-suffix aliases and DEBUG-only static/media
patterns, but it includes the DRF API root and every application route mounted
under `/api/`.

<!-- BEGIN FRONTEND AGENT ROUTE TABLE -->
| Route name | Path |
|---|---|
| `api-root` | `/api/` |
| `clear_processing_locks` | `/api/anonymization/clear-locks/` |
| `anonymization_document_types_dropdown` | `/api/anonymization/document-types/dropdown/` |
| `anonymization_items_overview` | `/api/anonymization/items/overview/` |
| `polling_coordinator_info` | `/api/anonymization/polling-info/` |
| `set_current_for_validation` | `/api/anonymization/{file_id}/current/` |
| `has_raw_video_file` | `/api/anonymization/{file_id}/has-raw/` |
| `start_anonymization` | `/api/anonymization/{file_id}/start/` |
| `get_anonymization_status` | `/api/anonymization/{file_id}/status/` |
| `validate_anonymization` | `/api/anonymization/{file_id}/validate/` |
| `audit_ledger_integrity` | `/api/audit-ledger/integrity/` |
| `auth-bootstrap` | `/api/auth/bootstrap` |
| `center-list` | `/api/centers/` |
| `center-detail` | `/api/centers/{pk}/` |
| `check_pe_exist` | `/api/check_pe_exist/{pk}/` |
| `findingclassification-list` | `/api/classifications/` |
| `get_choices_for_classification` | `/api/classifications/{classification_id}/choices/` |
| `findingclassification-detail` | `/api/classifications/{pk}/` |
| `csrf_token` | `/api/conf/` |
| `public_home` | `/api/endoreg_db/` |
| `examination-list` | `/api/examinations/` |
| `examination_stats` | `/api/examinations/stats/` |
| `get_interventions_for_finding` | `/api/examinations/{exam_id}/findings/{finding_id}/interventions/` |
| `get_indications_for_examination` | `/api/examinations/{exam_id}/indications/` |
| `get_interventions_for_examination` | `/api/examinations/{exam_id}/interventions/` |
| `get_findings_for_examination` | `/api/examinations/{examination_id}/findings/` |
| `examination-detail` | `/api/examinations/{pk}/` |
| `finding-list` | `/api/findings/` |
| `finding-get-finding-by-id` | `/api/findings/by-id/{finding_id}/` |
| `get_classifications_for_finding` | `/api/findings/{finding_id}/classifications/` |
| `finding-detail` | `/api/findings/{pk}/` |
| `finding-location-classifications` | `/api/findings/{pk}/location_classifications/` |
| `finding-morphology-classifications` | `/api/findings/{pk}/morphology_classifications/` |
| `gender-list` | `/api/genders/` |
| `gender-detail` | `/api/genders/{pk}/` |
| `get_indication_choices` | `/api/indications/{indication_id}/choices/` |
| `keycloak_login` | `/api/login/` |
| `keycloak_callback` | `/api/login/callback/` |
| `media_management_cleanup` | `/api/media-management/cleanup/` |
| `force_remove_media` | `/api/media-management/force-remove/{file_id}/` |
| `reset_processing_status` | `/api/media-management/reset-status/{file_id}/` |
| `media_management_status` | `/api/media-management/status/` |
| `frame-annotations-bulk-upsert` | `/api/media/annotations/frames/bulk-upsert/` |
| `frame-annotations-random-task` | `/api/media/annotations/frames/random-task/` |
| `frame-annotations-skip` | `/api/media/annotations/frames/skip/` |
| `hub-transfer-create` | `/api/media/hub/transfers/` |
| `hub-transfer-media-upload` | `/api/media/hub/transfers/{transfer_key}/media/` |
| `hub-transfer-status` | `/api/media/hub/transfers/{transfer_key}/status/` |
| `patient-media-timeline` | `/api/media/patients/{patient_id}/timeline/` |
| `pdf-list` | `/api/media/pdfs/` |
| `pdf-sensitive-metadata-list` | `/api/media/pdfs/sensitive-metadata/` |
| `pdf-detail` | `/api/media/pdfs/{pk}/` |
| `pdf-apply-redactions` | `/api/media/pdfs/{pk}/apply-redactions/` |
| `pdf-case-resolution` | `/api/media/pdfs/{pk}/case-resolution/` |
| `pdf-processing-history` | `/api/media/pdfs/{pk}/processing-history/` |
| `report-reimport` | `/api/media/pdfs/{pk}/reimport/` |
| `pdf-sensitive-metadata` | `/api/media/pdfs/{pk}/sensitive-metadata/` |
| `pdf-sensitive-metadata-verify` | `/api/media/pdfs/{pk}/sensitive-metadata/verify/` |
| `pdf-stream` | `/api/media/pdfs/{pk}/stream/` |
| `sm-pk` | `/api/media/sensitive-media-id/{pk}/{media_type}/` |
| `sensitive-metadata-list` | `/api/media/sensitive-metadata/` |
| `video-list` | `/api/media/videos/` |
| `video-segments-ensure-prediction-annotations` | `/api/media/videos/ensure-prediction-segment-annotations/` |
| `video-segments-ensure-annotations` | `/api/media/videos/ensure-segment-annotations/` |
| `video-annotated-export` | `/api/media/videos/export-annotated/` |
| `video-label-set-list` | `/api/media/videos/label-sets/list/` |
| `get_lvs_list` | `/api/media/videos/labels/list/` |
| `video-prediction-model-list` | `/api/media/videos/prediction-models/list/` |
| `video-segments-stats` | `/api/media/videos/segments/stats/` |
| `video-correction` | `/api/media/videos/video-correction/{pk}` |
| `video-detail-stream` | `/api/media/videos/{pk}/` |
| `video-apply-mask` | `/api/media/videos/{pk}/apply-mask/` |
| `video-case-resolution` | `/api/media/videos/{pk}/case-resolution/` |
| `video-detail` | `/api/media/videos/{pk}/details/` |
| `video-segment-ensure-prediction-annotations` | `/api/media/videos/{pk}/ensure-prediction-segment-annotations/` |
| `video-segment-ensure-annotations` | `/api/media/videos/{pk}/ensure-segment-annotations/` |
| `video-fps` | `/api/media/videos/{pk}/fps/` |
| `video-metadata` | `/api/media/videos/{pk}/metadata/` |
| `video-reimport` | `/api/media/videos/{pk}/reimport/` |
| `video-remove-frames` | `/api/media/videos/{pk}/remove-frames/` |
| `video-segments-by-video` | `/api/media/videos/{pk}/segments/` |
| `video-segments-blacken-outside` | `/api/media/videos/{pk}/segments/blacken-outside/` |
| `video-segments-bulk-mutation` | `/api/media/videos/{pk}/segments/bulk/` |
| `video-segments-import-predictions` | `/api/media/videos/{pk}/segments/import-predictions/` |
| `video-segments-rerun-predictions` | `/api/media/videos/{pk}/segments/rerun-predictions/` |
| `video-segments-validate-bulk` | `/api/media/videos/{pk}/segments/validate-bulk/` |
| `video-segments-validation-status` | `/api/media/videos/{pk}/segments/validation-status/` |
| `video-segment-detail` | `/api/media/videos/{pk}/segments/{segment_id}/` |
| `video-segment-validate` | `/api/media/videos/{pk}/segments/{segment_id}/validate/` |
| `video-sensitive-metadata` | `/api/media/videos/{pk}/sensitive-metadata/` |
| `video-sensitive-metadata-verify` | `/api/media/videos/{pk}/sensitive-metadata/verify/` |
| `video-stream` | `/api/media/videos/{pk}/stream/` |
| `video-frame-stream` | `/api/media/videos/{video_id}/frames/{frame_number}/stream/` |
| `patientexaminationreport-list` | `/api/patient-examination-reports/` |
| `patientexaminationreport-history-context` | `/api/patient-examination-reports/history-context/` |
| `patientexaminationreport-save-submission` | `/api/patient-examination-reports/save-submission/` |
| `patientexaminationreport-segment-frame-selector` | `/api/patient-examination-reports/segment-frame-selector/` |
| `patientexaminationreport-detail` | `/api/patient-examination-reports/{pk}/` |
| `patientexamination-list` | `/api/patient-examinations/` |
| `patient_examination_create` | `/api/patient-examinations/create/` |
| `patientexamination-examinations-dropdown` | `/api/patient-examinations/examinations_dropdown/` |
| `patient_examination_list` | `/api/patient-examinations/list/` |
| `patientexamination-patients-dropdown` | `/api/patient-examinations/patients_dropdown/` |
| `patientexamination-recent` | `/api/patient-examinations/recent/` |
| `get_classifications_for_examination` | `/api/patient-examinations/{exam_id}/classifications/` |
| `get_patient_examination_findings` | `/api/patient-examinations/{examination_id}/findings/` |
| `patient_examination_detail` | `/api/patient-examinations/{pk}/` |
| `patientexamination-details` | `/api/patient-examinations/{pk}/details/` |
| `patientexamination-draft` | `/api/patient-examinations/{pk}/draft/` |
| `patientfinding-list` | `/api/patient-findings/` |
| `patientfinding-get-patient-findings-by-examination` | `/api/patient-findings/by-examination/{patient_examination_id}/` |
| `patientfinding-get-patient-finding-by-id` | `/api/patient-findings/by-id/{patient_finding_id}/` |
| `patientfinding-detail` | `/api/patient-findings/{pk}/` |
| `patient-list` | `/api/patients/` |
| `patient-patient-count` | `/api/patients/patient_count/` |
| `patient-detail` | `/api/patients/{pk}/` |
| `patient-check-deletion-safety` | `/api/patients/{pk}/check_deletion_safety/` |
| `patient-generate-pseudonym` | `/api/patients/{pk}/pseudonym/` |
| `application_settings_detail` | `/api/settings/application/` |
| `application_settings_ai_dataset_export` | `/api/settings/application/ai_dataset_export/` |
| `application_settings_backup` | `/api/settings/application/backup/` |
| `application_settings_ai_datasets_dropdown` | `/api/settings/application/dropdowns/ai_datasets/` |
| `application_settings_annotators_dropdown` | `/api/settings/application/dropdowns/annotators/` |
| `application_settings_centers_dropdown` | `/api/settings/application/dropdowns/centers/` |
| `application_settings_network_node_roles_dropdown` | `/api/settings/application/dropdowns/network_node_roles/` |
| `application_settings_processors_dropdown` | `/api/settings/application/dropdowns/processors/` |
| `application_settings_report_templates_dropdown` | `/api/settings/application/dropdowns/report_templates/` |
| `application_settings_model_training_options` | `/api/settings/application/model_training/options/` |
| `application_settings_model_training_runs` | `/api/settings/application/model_training/runs/` |
| `application_settings_model_training_run_detail` | `/api/settings/application/model_training/runs/{run_id}/` |
| `application_settings_network_nodes` | `/api/settings/application/network_nodes/` |
| `application_settings_network_node_detail` | `/api/settings/application/network_nodes/{pk}/` |
| `application_settings_video_dimension_backfill_runs` | `/api/settings/application/video_dimension_backfill/runs/` |
| `application_settings_video_dimension_backfill_run_detail` | `/api/settings/application/video_dimension_backfill/runs/{run_id}/` |
| `general_stats` | `/api/stats/` |
| `video_upload` | `/api/upload/` |
| `upload_status` | `/api/upload/{id}/status/` |
| `video_segment_stats` | `/api/video-segment/stats/` |
| `video_segments_stats` | `/api/video-segments/stats/` |
| `sensitive_meta_stats` | `/api/video/sensitivemeta/stats/` |
<!-- END FRONTEND AGENT ROUTE TABLE -->

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

- Requirements evaluation:
  - no `/api/evaluate-requirements/` route is mounted in the current
    `endoreg_db` resolver.

- Report export:
  - use `media.pdfExportProcessed(pk)` (preferred) or `media.pdfExportRaw(pk)` for forced file download.

- Annotation/media export:
  - `POST /api/media/videos/export-annotated/` via `media.exportAnnotated`.
