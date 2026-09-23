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

## lx-dtypes Model Standardization Workflow

The binding readiness record for this work is feature
`lx_dtypes_model_standardization` in
`feature-tracking/LxDtypesModelStandardization.yml`. This section describes how
to perform and review the work; it does not carry an independent completion
status.

Every Django model is recorded in
`quality/lx_dtypes_model_inventory.yml`. The inventory is checked against both
the Django app registry and statically declared abstract models by
`scripts/check_lx_dtypes_model_inventory.py`. A model must have exactly one
target:

| Target | Use when | Required evidence |
| --- | --- | --- |
| `shared_lx_dtypes_contract` | The shape has clinical, semantic, interoperability, or cross-repository meaning. | The owning `lx_dtypes` contract plus the Endoreg adapter, serializer, or service that consumes it. |
| `local_boundary_schema` | The shape is specific to Endoreg persistence, jobs, storage operations, or legacy normalization. | The strict local schema plus every model boundary that invokes it. |
| `persistence_only` | The model owns relational columns, constraints, or an abstract persistence base and has no independent payload shape. | The model constraint or abstract base and a consumer review showing that no exchange contract is being duplicated. |
| `temporary_exception` | Migration cannot be completed safely in the current compatibility window. | Owner, concrete reason, expiry condition, and tests that freeze rather than expand the debt. |
| `unclassified` | Ownership review is incomplete. | This is active debt and cannot be used as completion evidence. |

Classification identifies the target architecture; it does not assert that all
fields are already validated. Put unresolved field-level gaps in `rationale`.
For example, `VideoFile` targets its local persistence schema while the
unvalidated `sequences` field remains an explicit gap.

Use this decision order:

1. Inventory all writers and readers, including managers, bulk operations,
   migrations, commands, tasks, serializers, views, imports, exports, and
   dynamic string references.
2. Decide whether the shape is shared or Endoreg-specific. Cross-repository
   clinical meaning takes precedence over the convenience of an existing local
   schema.
3. Select one canonical validator. A local adapter may translate legacy input
   or framework exceptions, but must not reimplement different field rules
   already owned by `lx_dtypes`.
4. Validate once at the owning input boundary. Persisted JSON is additionally
   canonicalized at the model boundary so direct ORM writes cannot store a
   competing representation.
5. Pass only the validated typed object or its canonical dump inward. Do not
   carry the original mapping in parallel.
6. Record ownership evidence and run the inventory guard before review.

`MedicalLedgerWriteReceipt` is the local-boundary example. Its
`MedicalLedgerRecordIds` schema owns the Endoreg row-identity payload; the model
canonicalizes `record_ids` during `save()`, while the service uses
`full_clean()` for the database constraints around the idempotency receipt.
The schema is not duplicated in the service.

### Risk-Ordered Cohort Execution

An execution cohort contains one to twelve closely related models. Larger
domains must be split before implementation. Every cohort review records:

- exact model labels and fields;
- current and canonical contracts;
- known API, import, export, service, job, persistence, and external consumers;
- migrations, backfills, package versions, and preceding cohort dependencies;
- Endoreg, `lx_dtypes`, security, and clinical owners as applicable;
- measurable completion criteria and the focused verification command;
- remaining gaps, each either unresolved or a time-bounded exception with an
  owner, reason, and expiry condition.

The domain queue is risk-ordered. It sets sequencing but does not claim
completion:

| Priority | Domain queue | Initial model slice | Canonical direction | Dependencies and required evidence |
| --- | --- | --- | --- | --- |
| 1 | Clinical identity and medical ledger | `Patient`, `SensitiveMeta`, `PatientExamination`, then the patient disease, event, finding, laboratory, and medication aggregates | Shared clinical shapes in `lx_dtypes`; Endoreg-only row identities and database constraints remain local. | Compatible medical-ledger package, center/patient-scope tests, identity minimization review, migration and rollback evidence. |
| 2 | Import boundaries | `UploadJob`, `ReportImportAttempt`, `RawPdfFile`, and import metadata on `VideoFile` | Strict local orchestration schemas consuming shared upload, report, and media contracts. | Identity cohort, idempotent concurrent import tests, quarantine behavior, encrypted-storage review. |
| 3 | Reports | `PatientExaminationReport`, `RawPdfFile`, `PdfProcessingHistory`, and concrete document models | Shared report submission, history, anonymization, and redaction contracts with one Endoreg persistence adapter. | Import cohort, persisted-JSON backfill if required, API compatibility and report round-trip tests. |
| 4 | Video | `VideoFile`, `VideoMeta`, `FFMpegMeta`, `VideoProcessingHistory`, `VideoHlsArtifact`, segments, and frames | Shared media contracts plus the versioned Endoreg storage profile. | Report/import stability, presentation-timestamp inventory, lease/concurrency tests, clinical quality and encrypted-storage approvals. |
| 5 | Hub transfer | `TransferJob`, `QuarantineItem`, `NetworkNode`, and hub provenance fields | Shared transfer semantics with local persistence and authorization adapters. | Anonymized-only export, mutual TLS profile, center-scope tests, mixed-version transfer tests. |
| 6 | Configuration and knowledge base | `ApplicationSettings`, `ModelMeta`, terminology models, and YAML-backed lookup models | Shared configuration and knowledge-base contracts; database loading remains an Endoreg service concern. | Pinned package and knowledge-base versions, `load_base_db_data` tests, configuration rollback. |
| 7 | Annotation and artificial-intelligence datasets | Annotation models, `LabelVideoSegment`, `AIDataSet`, `AIModelTrainingRun`, and `AIDataSetExportArtifact` | Shared annotation/export payloads; queueing and persistence orchestration remain local services. | Video cohort, deterministic frame identity, export data-minimization and round-trip tests. |
| 8 | Remaining persistence-only lookups | Exact groups selected from the still-unclassified inventory, never the whole remainder at once | `persistence_only` only after consumer search proves there is no duplicated exchange shape. | Clean static/dynamic consumer search, natural-key/data-loading tests, no unreviewed exception. |

The first reference execution cohort is exactly
`endoreg_db.MedicalLedgerWriteReceipt.record_ids`:

- current and canonical contract:
  `endoreg_db.schemas.medical_ledger.MedicalLedgerRecordIds`;
- consumers: `endoreg_db.services.medical_ledger` create/replay paths and the
  medical-ledger API;
- migration: `0058_medicalledgerwritereceipt`, dependent on the published
  medical-ledger contract used by the service;
- owners: Endoreg maintainers for persistence, `lx_dtypes` maintainers for the
  surrounding shared medical aggregate, and security/clinical reviewers for
  identity semantics;
- completion evidence: classified inventory entry, canonical model save,
  invalid direct-write rejection, transaction rollback, replay and parallel
  idempotency tests, Pyright, and the inventory guard;
- remaining cohort debt: none. A future bulk writer is a new write boundary and
  must add validation and tests before use.

New workflow behavior remains in services throughout every cohort. Models may
keep persistence validation and thin typed state transitions, but a cohort must
not add a model-to-service dependency. Likewise, `lx_dtypes` must not import
Django models or Endoreg services. The model import-boundary test and a reverse
dependency search of the `lx_dtypes` source tree are required cohort evidence.

Run the guard and its focused verification from the repository root:

```bash
devenv shell -- .devenv/state/venv/bin/python \
  scripts/check_lx_dtypes_model_inventory.py
.devenv/state/venv/bin/pyright
devenv shell -- .devenv/state/venv/bin/pytest \
  tests/scripts/test_check_lx_dtypes_model_inventory.py -q
```

### Model And Field Review Checklist

For every new model, and for every added, renamed, optionalized, or removed
field:

1. Update the inventory structure, target, owner, rationale, and stable
   ownership evidence.
2. Search static imports, dynamic references, and all API, import, export, job,
   service, persistence, and repository-external consumers.
3. Update the Django field/constraint/migration, boundary schema, shared
   contract, serializer, loader/dumper, and service signatures that apply.
4. Preserve snake_case and add explicit legacy normalization only at one named
   boundary.
5. Review data minimization, center and patient scope, direct identifiers,
   secrets, encrypted storage, raw-media prohibition, and fail-closed error
   behavior.
6. Define compatibility, backfill, quarantine, rollback, and mixed-version
   behavior before changing a persisted or public shape.
7. Run Pyright before focused positive, invalid-input, canonical round-trip,
   migration, import, concurrency, and integration tests.
8. Update the feature assessment only with stable evidence and a named
   assessor. Do not copy completion state into this document or another plan.

### Cross-Repository Release Procedure

The `lx_dtypes` maintainer owns shared contract semantics and package release;
the `endoreg_db` maintainer owns the persistence adapter, migration, and
deployment evidence. Security or clinical reviewers must approve changes that
affect identity, authorization, clinical meaning, anonymization, or media
integrity.

For a shared-contract change, use this order:

1. Add the versioned contract and valid/invalid/round-trip tests in
   `lx_dtypes`. Declare whether the change is additive or breaking and name the
   supported old/new version window.
2. Publish the reviewed `lx_dtypes` package. Do not make production depend on a
   source checkout.
3. Pin the compatible package version in Endoreg and add or update the single
   boundary adapter. Test both ends of any declared mixed-version window.
4. Deploy readers that accept the new canonical shape before deploying writers
   that emit it.
5. Run validation reporting and any required backfill. Invalid existing data
   is quarantined or fails closed; it is not silently rewritten.
6. Deploy writers, observe boundary failures and data-integrity metrics, and
   retain the rollback version for the declared window.
7. Remove a compatibility adapter only after all known consumers have migrated,
   the announced window has elapsed, and consumer search plus integration tests
   are clean.

Required production evidence is the package/version pin, contract and consumer
tests, migration/backfill output when applicable, security or clinical
approval when applicable, deployment observation, and a rehearsed rollback or
backup/restore reference. The feature tracker is the sole place where that
evidence changes readiness status.

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
| Video file lifecycle | central video model plus wrappers for IO, streaming, metadata, frames, anonymization, prediction, state | `endoreg_db/models/media/video/video_file.py`, `endoreg_db/services/video_files/__init__.py` |
| Raw PDF lifecycle | report model plus wrappers for IO, metadata, state, validation, creation, deletion | `endoreg_db/models/media/pdf/raw_pdf.py`, `endoreg_db/services/raw_pdf_files/__init__.py` |
| Frame extraction | frame cache manifests, validation, staging, record sync | `endoreg_db/services/video_files/_frames/_extract_frames.py` |
| Video anonymization | processed artifact generation, outside-frame blackening, raw cleanup | `endoreg_db/services/video_files/_anonymization.py` |
| Segment annotations | segment lifecycle, validation, frame extraction/deletion, generated annotations | `endoreg_db/models/label/label_video_segment/label_video_segment.py` |
| Frame annotation queue | task queueing, selection, serialization, annotation sync | `endoreg_db/models/state/frame_annotation.py` |
| AI datasets | active learning, manifests, frame buckets, export artifacts | `endoreg_db/models/aidataset/aidataset.py` |
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

Current broad import count from `rg '^\\s*from endoreg_db\\.models import ' endoreg_db -g '*.py'`:

- 41 matching import statements, all inside `endoreg_db/models`.
- No matching statements in `endoreg_db/services`, `endoreg_db/views`,
  management commands, or serializers.

This is a source snapshot, not a quality target. Re-run the command before using
the number as evidence because multiline imports and dynamic imports require
separate review.

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

### Retired Query Facades

Query-only model facades were removed after repository-wide and lx-annotate
consumer scans found no remaining callers. Use the service-owned query
contracts:

- replace `VideoFile.check_hash_exists(...)` with
  `endoreg_db.services.video_files.queries.video_hash_exists(...)`;
- replace `VideoFile.get_video_by_pk(...)` and
  `VideoFile.get_video_by_content_hash(...)` with the corresponding functions
  in `endoreg_db.services.video_files.queries`;
- replace `RawPdfFile.get_report_by_pk(...)` and
  `RawPdfFile.get_report_by_hash(...)` with `get_raw_pdf_by_pk(...)` and
  `get_raw_pdf_by_content_hash(...)` from
  `endoreg_db.services.raw_pdf_files.queries`.

The unused `VideoFile.get_all_videos()` and
`VideoFile.count_unmodified_others()` facades and their service helpers were
removed without replacement. Consumers should express owned query semantics
through a domain service instead of reintroducing global model enumeration.

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

The `AIDataSet` model imports neutral export and frame-bucket contracts directly
from their owning `lx_dtypes.models.contracts` modules. Only the three retained
workflow compatibility methods import the endoreg-db dataset services. Do not
route neutral contract types back through those service modules.

## Workflow Responsibility Ranking

This ranking is based on workflow responsibility, not just line count.

1. `endoreg_db/models/media/video/video_file.py` - central video schema and compatibility facade for video lifecycle operations.
2. `endoreg_db/models/media/pdf/raw_pdf.py` - report schema and compatibility facade for report lifecycle operations.
3. `endoreg_db/models/aidataset/aidataset.py` - active learning, manifests, exports, training runs, artifacts.
4. `endoreg_db/models/state/frame_annotation.py` - frame task queue, serialization, annotation sync.
5. `endoreg_db/models/label/label_video_segment/label_video_segment.py` - segment lifecycle, validation, frame actions, generated annotations.
6. `endoreg_db/services/video_files/_anonymization.py` - anonymized artifact generation and raw cleanup.
7. `endoreg_db/services/video_files/_frames/_extract_frames.py` - frame extraction and cache integrity.
8. `endoreg_db/models/metadata/sensitive_meta_logic.py:153` - patient identity and pseudonymization workflow logic.
9. `endoreg_db/models/state/video.py:19` - video processing state transitions and export readiness.
10. `endoreg_db/models/hub/transfer_job.py:17` - transfer policy/status model and provenance validation boundary.

## Security And Storage Notes

- `VideoFile.raw_file` and `processed_file` use `LazyEncryptedStorage` in
  `endoreg_db/models/media/video/video_file.py`.
- `RawPdfFile.file` and `processed_file` use `LazyEncryptedStorage` in
  `endoreg_db/models/media/pdf/raw_pdf.py`.
- `VideoStorageMode` is typed in
  `endoreg_db/models/media/video/storage_mode.py:8`.
- Raw media transfer enum values exist in
  `endoreg_db/models/hub/transfer_job.py:17`, but the API rejects raw media
  transfer modes in `endoreg_db/serializers/hub/transfer_job.py:64`.
- `UploadJob.processing_provenance` is a `JSONField` validated in `clean()` in
  `endoreg_db/models/hub/upload_job.py`.
- `TransferJob.provenance` is a `JSONField` validated in `clean()` in
  `endoreg_db/models/hub/transfer_job.py`.
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
