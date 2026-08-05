# API Route Test Matrix

> Status tracking was migrated to `feature-tracking/ApiContracts.yml`. This
> document is a coverage reference and must not carry an independent completion
> status.

This matrix tracks high-risk API routes and the backend tests that exercise them.
Routes are shown with the canonical `/endoreg-api/` mount. `/api/` remains a
compatibility alias during migration.

## Reproducible Contract Check

Run the versioned matrix against Django's real URL inventory from the repository
root:

```bash
.devenv/state/venv/bin/python scripts/check_route_matrix.py
```

The check fails when this document is missing, malformed, references a missing
test file, contains duplicate canonical routes, or documents a route that is
absent from `manage.py show_urls --format csv`. It no longer depends on an
unversioned lx-annotate temporary file. `--matrix <legacy.json>` remains
supported for older automation, while `--urls-csv <file>` permits deterministic
offline validation of this Markdown contract.

## Mount Ownership And Compatibility

| Mount | Owner | Support contract | Removal gate |
|---|---|---|---|
| `/endoreg-api/` | `endoreg_db` | Canonical mount for the locally hosted REST Framework and Ninja endpoints. New consumers must build paths through `endoregApi()` or the temporary `r()` wrapper around it. | Canonical; not scheduled for removal. |
| `/api/` | `endoreg_db` | Compatibility mount of the same URL configuration. It is accepted for existing deployments only and must not appear as a direct string literal in new `lx-annotate` runtime code. | Remove only after the frontend contract scan is clean, every deployment sets the canonical prefix, reverse-proxy routes are migrated, and an announced compatibility window has elapsed. |
| `/dtypes-api/` | `lx_dtypes`, hosted by `endoreg_db` | Canonical mount for terminology, finding, classification, and typed-record contracts. New consumers must use `dtypesApi()`. | Canonical; not scheduled for removal. |
| `/base_api/` | `lx_dtypes` | Upstream compatibility mount retained by the `lx_dtypes` URL configuration. It must not be nested below `/endoreg-api/`. | Remove only in a coordinated `lx_dtypes` contract release after all host deployments and consumers use `/dtypes-api/`, compatibility tests are migrated, and the announced support window has elapsed. |

`endoreg_db.utils.api_urls` owns the backend prefix constants.
`lx-annotate/frontend/src/api/axiosInstance.ts` owns frontend path construction
and deployment overrides. A deployment may temporarily select a compatibility
prefix, but feature code must not bypass these boundaries with a literal mount.

## Common Wire Contract

- Public paths are currently unversioned. Compatibility is controlled through
  canonical mounts, explicit aliases, typed schemas, and announced migrations;
  a breaking payload change requires a new versioned contract or a compatibility
  adapter rather than an in-place reinterpretation.
- Backend and wire payload fields use `snake_case`. The central `lx-annotate`
  Axios boundary converts request keys to `snake_case` and JSON response keys to
  `camelCase`; endpoint code must not add a second conversion layer.
- Authentication and authorization are route-specific and fail closed.
  Unauthenticated requests return `401` or the configured OpenID Connect flow;
  authenticated callers without the required role or scope return `403`.
  Center-scoped resources may return `404` to avoid disclosing foreign object
  existence.
- Validation failures use structured `4xx` JSON responses. Expected error
  payloads expose a stable `detail`, field-error, or approved `error` message;
  stack traces, local paths, secrets, and raw clinical media are not response
  payloads.
- Media, upload, report, administration, and terminology routes retain their
  domain-specific permission and integrity tests listed below. The compatibility
  mount does not weaken those controls because it resolves the same URL
  configuration.

## Sensitive Media + Streaming

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/media/videos/<pk>/sensitive-metadata/` | Read/patch video-linked sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/endoreg-api/media/videos/<pk>/sensitive-metadata/verify/` | Verify video sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/endoreg-api/media/pdfs/<pk>/sensitive-metadata/` | Read/patch report-linked sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/endoreg-api/media/pdfs/<pk>/sensitive-metadata/verify/` | Verify report sensitive metadata | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/endoreg-api/media/sensitive-media-id/<pk>/<media_type>/` | Resolve media item -> sensitive metadata id | `tests/views/anonymization/test_sensitive_metadata_view.py` |
| `/endoreg-api/media/pdfs/<pk>/stream/` | report stream (raw/processed, nginx handoff) | `tests/views/report/test_report_stream.py` |
| `/endoreg-api/media/videos/<pk>/stream/` | Video stream (raw/processed, nginx handoff) | `tests/views/video/test_video_stream.py` |
| `/endoreg-api/media/videos/<video_id>/frames/<frame_number>/stream/` | Frame stream | `tests/views/media/test_frame_stream.py` |

## Video Annotation Timeline

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/media/videos/<pk>/timeline/frame-neighborhood/` | Resolve a bounded, cacheable display-frame window through the canonical PTS timeline with a constant four-query budget | `tests/views/video/test_video_timeline_view.py`, `tests/services/test_segment_frame_extraction.py` |

## Media Management

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/media-management/status/` | Aggregate cleanup/status overview | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/endoreg-api/media-management/cleanup/` | Cleanup dry-run/force operations | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/endoreg-api/media-management/force-remove/<file_id>/` | Force delete media by id | `tests/views/anonymization/test_media_management_endpoints.py` |
| `/endoreg-api/media-management/reset-status/<file_id>/` | Reset processing state | `tests/views/anonymization/test_media_management_endpoints.py` |

## Settings

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/settings/application/` | Read/patch application defaults | `tests/views/misc/test_application_settings_endpoints.py` |
| `/endoreg-api/settings/application/dropdowns/centers/` | Centers dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/endoreg-api/settings/application/dropdowns/processors/` | Processors dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/endoreg-api/settings/application/dropdowns/annotators/` | Annotators dropdown | `tests/views/misc/test_application_settings_endpoints.py` |
| `/endoreg-api/settings/application/dropdowns/report_templates/` | Report templates dropdown | `tests/views/misc/test_application_settings_endpoints.py` |

## Stats

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/examinations/stats/` | Examination dashboard stats | `tests/views/misc/test_stats_endpoints.py` |
| `/endoreg-api/video-segment/stats/` | Segment stats (singular alias) | `tests/views/misc/test_stats_endpoints.py` |
| `/endoreg-api/video-segments/stats/` | Segment stats (plural) | `tests/views/misc/test_stats_endpoints.py` |
| `/endoreg-api/video/sensitivemeta/stats/` | Sensitive metadata dashboard stats | `tests/views/misc/test_stats_endpoints.py` |
| `/endoreg-api/stats/` | General dashboard stats | `tests/views/misc/test_stats_endpoints.py` |

## Upload

| Route | Purpose | Test Coverage |
|---|---|---|
| `/endoreg-api/upload/` | Create upload job with file validation | `tests/views/misc/test_upload_endpoints.py` |
| `/endoreg-api/upload/<uuid:id>/status/` | Poll upload job status | `tests/views/misc/test_upload_endpoints.py` |
