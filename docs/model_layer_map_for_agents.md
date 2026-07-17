# Model Layer Map For Agents

This map is for agents preparing to touch `endoreg_db/models`. Read it before
moving model methods, changing imports, or adding workflow behavior to models.

## Working Rule

Treat `endoreg_db/models` as the persistence and invariant layer. New workflow
logic belongs in `endoreg_db/services`, with models keeping fields,
constraints, typed state transitions, and thin compatibility wrappers only.

When changing model-layer imports:

- Prefer explicit leaf imports over `from endoreg_db.models import ...`.
- Do not add new broad `endoreg_db.models` barrel imports.
- Do not add new `models -> services` imports unless preserving an existing
  compatibility wrapper.
- Do not reintroduce legacy private model implementation modules such as
  `video_file_io`, `video_file_anonymize`, or `video_file_frames/_*.py`.
- Keep persisted JSON validation at the model boundary using typed schemas.
- Keep storage routing typed through enums such as `VideoStorageMode`.
- Keep raw media export and raw media transfer prohibited.

## Typed Boundary Rules

External input is normalized exactly once at the owning boundary. DRF request
data, JSON/YAML, persisted JSON, environment values, and third-party responses
must be validated into one typed internal shape before workflow code consumes
them. Do not pass the original mapping alongside the validated object.

Field ownership is split deliberately:

| Layer | Owns | Must not own |
| --- | --- | --- |
| Django model | persisted fields, database constraints, model-boundary validation, typed state transitions | request parsing, export orchestration, or service workflows |
| endoreg_db boundary schema | API/file/persisted-JSON validation and legacy normalization | ORM queries or persistence behavior |
| lx_dtypes | shared cross-repository clinical and interoperability contracts | endoreg_db-specific database behavior |
| DRF serializer/view | HTTP representation, authentication, and conversion into the boundary schema | a second competing domain shape |
| service | typed workflow and explicit side effects | unvalidated external mappings |

Apply these field rules:

- Prefer `list[T]` for semantic lists. A string containing serialized list data
  is a transport compatibility field and needs a named boundary adapter; do not
  expose `str | list[T]` beyond that adapter.
- Optional fields require a domain reason. Distinguish “not supplied” from an
  explicit empty value when partial updates depend on that difference.
- Pydantic mixins must not define `model_config`. Multiple parents must not
  define the same field unless the overriding class documents and tests the
  intended MRO behavior.
- Strict external and clinical contracts use `extra="forbid"`. Evolving
  internal carriers may allow compatibility keys only through an explicit,
  tested normalization function.
- `.links` and comparable relationship properties are query-producing unless a
  test demonstrates otherwise. Hot paths need one documented loader with the
  matching `select_related`/`prefetch_related` plan and a bounded query-count
  test.

`PatientExamination` no longer exposes a `.links` model property. Load the graph
through `endoreg_db.services.patient_examination_links.load_patient_examination_for_links`
and pass the result to `build_patient_examination_links`. The loader performs the
bounded object-graph query plan; service-owned aggregation then performs zero
additional queries and fails loudly for an incompletely loaded instance. This
keeps the dependency direction at `service -> model`.

### Cross-Layer Field Change Checklist

For every added, renamed, made-optional, or removed field, record which entries
are applicable and update them in the same change:

1. Django field, constraint, migration, and model-boundary validation.
2. endoreg_db Pydantic schema and legacy normalization adapter.
3. Shared lx_dtypes contract and its version/compatibility window.
4. DRF serializer, request/response schema, and snake_case API contract.
5. DataDict, YAML/JSON loader, dump path, and persisted provenance shape.
6. Service signatures, exhaustive enum branches, and export consumers.
7. Query loader/prefetch paths when `.links` or evaluation traverses the field.
8. Positive, invalid-input, round-trip, query-count, and migration tests as
   applicable.
9. Backfill, rollback, and mixed-version deployment behavior for persisted
   changes.

If ownership is unclear or two layers would normalize the same value, stop and
resolve the ownership boundary before implementation.

The shared `lx_dtypes` layer enforces the inheritance rules in
`lx_dtypes/models/base/app_base_model/tests/test_inheritance_safety.py`. The
test imports the complete model package, rejects duplicate fields declared by
direct Pydantic parents, verifies MRO-wide list-field normalization and dumping,
and protects minimal inheritance for `FilesAndDirsModel`.

## Current Shape

`endoreg_db.models` is currently more than a schema package. Several model files
also act as workflow facades for video processing, report processing, dataset
export, annotation queueing, anonymization, frame extraction, and hub transfer
state.

The biggest big-ball-of-mud risk is bidirectional coupling:

```text
model facade -> service package -> private model implementation -> model facade
```

For video files, the private implementation has been inverted into
`endoreg_db/services/video_files/_*`. The legacy
`endoreg_db/models/media/video/video_file_*` compatibility aliases have been
retired and should not be reintroduced.

The import barrel at `endoreg_db/models/__init__.py` amplifies this risk by
pulling broad subpackages into otherwise small imports.

## Primary Hot Spots

| Area | Current role | First files to inspect |
| --- | --- | --- |
| Video file lifecycle | central video model plus wrappers for IO, streaming, metadata, frames, anonymization, prediction, state | `endoreg_db/models/media/video/video_file.py:44`, `endoreg_db/services/video_files/__init__.py:3` |
| Raw PDF lifecycle | report model plus wrappers for IO, metadata, state, validation, creation, deletion | `endoreg_db/models/media/pdf/raw_pdf.py:32`, `endoreg_db/services/raw_pdf_files/__init__.py:3` |
| Frame extraction | frame cache manifests, validation, staging, record sync | `endoreg_db/services/video_files/_frames/_extract_frames.py` |
| Video anonymization | processed artifact generation, outside-frame blackening, raw cleanup | `endoreg_db/services/video_files/_anonymization.py` |
| Segment annotations | segment lifecycle, validation, frame extraction/deletion, generated annotations | `endoreg_db/models/label/label_video_segment/label_video_segment.py:33` |
| Frame annotation queue | task queueing, selection, serialization, annotation sync | `endoreg_db/models/state/frame_annotation.py:83` |
| AI datasets | active learning, manifests, frame buckets, export artifacts | `endoreg_db/models/aidataset/aidataset.py:123` |
| Sensitive metadata | patient and examination identity, hashes, pseudo-patient creation | `endoreg_db/models/metadata/sensitive_meta.py:27`, `endoreg_db/models/metadata/sensitive_meta_logic.py:153` |
| Hub uploads/transfers | persisted provenance JSON, transfer status, raw-transfer policy surface | `endoreg_db/models/hub/upload_job.py:149`, `endoreg_db/models/hub/transfer_job.py:17` |

## Model Files Importing Services, Utils, Import Files, Helpers

These imports are not all wrong, but they are dependency-pressure points. Avoid
adding more. Prefer moving implementation into services and keeping models as
thin callers only where backward compatibility requires it.

| Model file | Imports from | References |
| --- | --- | --- |
| `administration/ai/ai_model.py` | services | `endoreg_db/models/administration/ai/ai_model.py:130` |
| `administration/person/examiner/examiner.py` | utils | `endoreg_db/models/administration/person/examiner/examiner.py:5` |
| `administration/person/patient/patient.py` | utils | `endoreg_db/models/administration/person/patient/patient.py:30`, `:102`, `:464` |
| `administration/product/product.py` | utils | `endoreg_db/models/administration/product/product.py:5`, `:6` |
| `aidataset/aidataset.py` | services | `endoreg_db/models/aidataset/aidataset.py:20`, `:26`, `:1240`, `:1258`, `:1274` |
| `hub/upload_job.py` | services, utils | `endoreg_db/models/hub/upload_job.py:10`, `:11`, `:12` |
| `hub/transfer_job.py` | services | `endoreg_db/models/hub/transfer_job.py:9` |
| `label/label_video_segment/label_video_segment.py` | services | `endoreg_db/models/label/label_video_segment/label_video_segment.py:10` |
| `media/pdf/raw_pdf.py` | services, utils | `endoreg_db/models/media/pdf/raw_pdf.py:12`, `:17`, `:18`, `:163`, `:171`, `:182`, `:190`, `:206`, `:215`, `:224`, `:241`, `:252`, `:263`, `:278`, `:289`, `:305`, `:321`, `:333`, `:346`, `:359`, `:364`, `:375`, `:381`, `:387` |
| `media/video/storage_mode.py` | utils | `endoreg_db/models/media/video/storage_mode.py:5` |
| `media/video/video_file.py` | services, utils | `endoreg_db/models/media/video/video_file.py:17`, `:21`, `:37`, `:200` through `:739` |
| `metadata/model_meta_logic.py` | utils | `endoreg_db/models/metadata/model_meta_logic.py:13` |
| `metadata/sensitive_meta_logic.py` | utils | `endoreg_db/models/metadata/sensitive_meta_logic.py:12`, `:15` |
| `metadata/video_prediction_meta.py` | services | `endoreg_db/models/metadata/video_prediction_meta.py:9` |
| `metadata/video_prediction_logic.py` | services | `endoreg_db/models/metadata/video_prediction_logic.py:6` |
| `state/frame_annotation.py` | services, utils | `endoreg_db/models/state/frame_annotation.py:14`, `:15`, `:156` |
| `state/video_segment_validation.py` | services | `endoreg_db/models/state/video_segment_validation.py:10` |
| `state/processing_history/processing_history.py` | utils | `endoreg_db/models/state/processing_history/processing_history.py:5` |

No model imports from `endoreg_db.serializers` were found in the current map.

## Service Files Importing Model-Private Implementation

Video service files should not import implementation from
`endoreg_db.models.media.video.video_file_*`. Those implementations now live
under `endoreg_db/services/video_files/_*`, and the model-side compatibility
modules for old imports and tests have been removed.

`endoreg_db/services/raw_pdf_files/*.py` may import the `RawPdfFile` leaf model
for persistence access, but should not import workflow implementation from
`endoreg_db.models.media.pdf.create_report_from_file`.

## Barrel Import Pressure

`endoreg_db/models/__init__.py` re-exports broad model families from
administration, labels, media, medical, metadata, state, datasets, and hub
models. See `endoreg_db/models/__init__.py:1`.

Current broad import count found during this map:

- 309 total `from endoreg_db.models import ...` imports.
- 113 inside `endoreg_db/models`.
- 50 inside `endoreg_db/services`.
- 47 inside `endoreg_db/views`.
- 40 inside management commands.
- 35 inside serializers.

Do not add more. When touching a file, prefer replacing only the imports already
needed for that local change.

## Circular And Near-Circular Risks

### Video

```text
models/media/video/video_file.py
  -> services/video_files/__init__.py
  -> services/video_files/{io,frames,metadata,anonymization,pipeline,ai,...}.py
  -> models/media/video/video_file.py for persistence access
```

Primary references:

- `endoreg_db/models/media/video/video_file.py:200`
- `endoreg_db/services/video_files/__init__.py:3`
- `endoreg_db/services/video_files/_io.py`
- `endoreg_db/services/video_files/_frames`
- `endoreg_db/services/video_files/_metadata`

### Report PDF

```text
models/media/pdf/raw_pdf.py
  -> services/raw_pdf_files/__init__.py
  -> services/raw_pdf_files/{io,imports,metadata,state,validation}.py
  -> models/media/pdf/raw_pdf.py
```

Primary references:

- `endoreg_db/models/media/pdf/raw_pdf.py:163`
- `endoreg_db/services/raw_pdf_files/__init__.py:3`
- `endoreg_db/services/raw_pdf_files/io.py:21`
- `endoreg_db/services/raw_pdf_files/imports.py:17`

### Dataset And Annotation

`AIDataSet`, `LabelVideoSegment`, `frame_annotation`, and video services share
workflow responsibilities across dataset export, frame queues, segment-derived
annotations, and frame extraction.

Primary references:

- `endoreg_db/models/aidataset/aidataset.py:20`
- `endoreg_db/models/label/label_video_segment/label_video_segment.py:10`
- `endoreg_db/models/state/frame_annotation.py:83`
- `endoreg_db/services/aidataset_exports.py:13`
- `endoreg_db/services/aidataset_frame_buckets.py:11`

## Workflow Responsibility Ranking

This ranking is based on workflow responsibility, not just line count.

1. `endoreg_db/models/media/video/video_file.py:44` - central video schema and compatibility facade for video lifecycle operations.
2. `endoreg_db/models/media/pdf/raw_pdf.py:32` - report schema and compatibility facade for report lifecycle operations.
3. `endoreg_db/models/aidataset/aidataset.py:123` - active learning, manifests, exports, training runs, artifacts.
4. `endoreg_db/models/state/frame_annotation.py:83` - frame task queue, serialization, annotation sync.
5. `endoreg_db/models/label/label_video_segment/label_video_segment.py:33` - segment lifecycle, validation, frame actions, generated annotations.
6. `endoreg_db/services/video_files/_anonymization.py` - anonymized artifact generation and raw cleanup.
7. `endoreg_db/services/video_files/_frames/_extract_frames.py` - frame extraction and cache integrity.
8. `endoreg_db/models/metadata/sensitive_meta_logic.py:153` - patient identity and pseudonymization workflow logic.
9. `endoreg_db/models/state/video.py:19` - video processing state transitions and export readiness.
10. `endoreg_db/models/hub/transfer_job.py:17` - transfer policy/status model and provenance validation boundary.

## Security And Storage Notes

- `VideoFile.raw_file` and `processed_file` use `LazyEncryptedStorage` at
  `endoreg_db/models/media/video/video_file.py:51` and `:58`.
- `RawPdfFile.file` and `processed_file` use `LazyEncryptedStorage` at
  `endoreg_db/models/media/pdf/raw_pdf.py:66` and `:72`.
- `VideoStorageMode` is typed in
  `endoreg_db/models/media/video/storage_mode.py:8`.
- Raw media transfer enum values exist in
  `endoreg_db/models/hub/transfer_job.py:17`, but the API rejects raw media
  transfer modes in `endoreg_db/serializers/hub/transfer_job.py:64`.
- `UploadJob.processing_provenance` is a `JSONField` at
  `endoreg_db/models/hub/upload_job.py:149` and is validated in `clean()` at
  `endoreg_db/models/hub/upload_job.py:274`.
- `TransferJob.provenance` is a `JSONField` at
  `endoreg_db/models/hub/transfer_job.py:163` and is validated in `clean()` at
  `endoreg_db/models/hub/transfer_job.py:198`.
- Hub provenance schemas live in
  `endoreg_db/services/hub/payloads.py:117` and
  `endoreg_db/services/hub/payloads.py:181`.

## Preferred Refactor Order

1. Add or keep import-boundary checks before moving behavior. Use a temporary
   allowlist for current debt instead of allowing new debt.
   - Current check: `tests/app/test_model_import_boundaries.py` records the
     existing `endoreg_db.models -> endoreg_db.services` import debt as an AST
     allowlist. Update the allowlist only when removing existing debt or when a
     consciously accepted compatibility wrapper is added.
2. Replace barrel imports in touched service files with explicit leaf imports.
   Start with `endoreg_db/services`, then serializers, then views.
3. Move neutral payload schemas out of service packages if models need them for
   boundary validation. Keep the typed validation at the model boundary.
4. Keep `video_files` inverted: implementation belongs in
   `services/video_files/*`; do not reintroduce `models/media/video/video_file_*`
   compatibility aliases.
5. Keep `raw_pdf_files` service-backed: `RawPdfFile` owns fields, validation,
   and thin wrappers; report workflow implementation belongs in
   `services/raw_pdf_files/*`.
6. Extract annotation and dataset workflows after media lifecycle is stable:
   `frame_annotation.py`, `label_video_segment.py`, and `aidataset.py`.
7. Only then reduce the model barrel. Do it incrementally by replacing imports
   in files already being touched.

## Do Not Break

- Do not transmit or persist a master key.
- Do not enable raw media export or raw media transfer.
- Do not bypass mTLS requirements.
- Do not use raw filesystem mutation where typed wrappers exist.
- Do not route storage using untyped strings.
- Do not persist JSON workflow payloads without typed schema validation at the
  boundary.
- Do not introduce camelCase API fields.
