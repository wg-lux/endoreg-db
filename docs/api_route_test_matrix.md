# API Route Test Matrix

This matrix tracks high-risk API routes and the backend tests that exercise them.

## Sensitive Media + Streaming

| Route | Purpose | Test Coverage |
|---|---|---|
| `/api/media/videos/<pk>/sensitive-metadata/` | Read/patch video-linked sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/api/media/videos/<pk>/sensitive-metadata/verify/` | Verify video sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/api/media/pdfs/<pk>/sensitive-metadata/` | Read/patch report-linked sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/api/media/pdfs/<pk>/sensitive-metadata/verify/` | Verify report sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/api/media/sensitive-media-id/<pk>/<media_type>/` | Resolve media item -> sensitive metadata id | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/api/media/pdfs/<pk>/stream/` | report stream (raw/processed, nginx handoff) | `tests/views/report/test_report_stream.py` |
| `/api/media/videos/<pk>/stream/` | Video stream (raw/processed, nginx handoff) | `tests/views/video/test_video_stream.py` |
| `/api/media/videos/<video_id>/frames/<frame_number>/stream/` | Frame stream | `tests/views/media/test_frame_stream.py` |

## Media Management

| Route | Purpose | Test Coverage |
|---|---|---|
| `/api/media-management/status/` | Aggregate cleanup/status overview | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/api/media-management/cleanup/` | Cleanup dry-run/force operations | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/api/media-management/force-remove/<file_id>/` | Force delete media by id | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/api/media-management/reset-status/<file_id>/` | Reset processing state | `tests/views/anonymization/test_media_management_endpoints.py` |

## Settings

| Route | Purpose | Test Coverage |
|---|---|---|
| `/api/settings/application/` | Read/patch application defaults | `tests/views/misc/test_application_settings_endpoints.py` |
| `/api/settings/application/dropdowns/centers/` | Centers dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/api/settings/application/dropdowns/processors/` | Processors dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/api/settings/application/dropdowns/annotators/` | Annotators dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/api/settings/application/dropdowns/report_templates/` | Report templates dropdown | `tests/views/misc/test_application_settings_endpoints.py` |

## Stats

| Route | Purpose | Test Coverage |
|---|---|---|
| `/api/examinations/stats/` | Examination dashboard stats | `tests/views/misc/test_stats_endpoints.py` |
| `/api/video-segment/stats/` | Segment stats (singular alias) | `tests/views/misc/test_stats_endpoints.py` |
| `/api/video-segments/stats/` | Segment stats (plural) | `tests/views/misc/test_stats_endpoints.py` |
| `/api/video/sensitivemeta/stats/` | Sensitive metadata dashboard stats | `tests/views/misc/test_stats_endpoints.py` |
| `/api/stats/` | General dashboard stats | `tests/views/misc/test_stats_endpoints.py` |

## Upload

| Route | Purpose | Test Coverage |
|---|---|---|
| `/api/upload/` | Create upload job with file validation | `tests/views/misc/test_upload_endpoints.py` |
| `/api/upload/<uuid:id>/status/` | Poll upload job status | `tests/views/misc/test_upload_endpoints.py` |
