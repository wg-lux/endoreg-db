# Video Typing Integration Context

> Status tracking moved to `feature-tracking/done/TypeSafety.yml`. This
> document is retained as technical context and does not maintain an
> independent completion status.

## Objective

The original objective was to introduce stronger `lx-dtypes` typing at video
processing boundaries without directly replacing the existing Django models.
The selected pattern is a typed boundary:

- the Django object-relational mapper remains the source of truth;
- `lx-dtypes` supplies strict Pydantic contracts for export, application
  programming interface (API) boundaries, and internal normalization.

## Original Assessment

### Video State

`endoreg-db` represents video state with multiple Boolean fields in
`endoreg_db/models/state/video.py`. `VideoState.anonymization_status` provides
a derived enumeration view, while `lx-dtypes` provides its canonical state
type under `lx_dtypes.models.ledger.p_video`.

The typed view reduces implicit Boolean combinations and gives API filters and
job orchestration a stable state representation.

### Video Segments

The relevant Endoreg boundaries are:

- `endoreg_db/models/label/label_video_segment/label_video_segment.py`;
- `endoreg_db/serializers/label_video_segment/label_video_segment.py`;
- `endoreg_db/services/segment_sync.py`.

The corresponding `lx-dtypes` contract requires a label and label set.
Endoreg can contain segments without a label, and their label set may be only
indirectly resolvable. A direct one-for-one model replacement is therefore not
valid; an adapter must resolve and validate the contract explicitly.

### Sensitive Metadata

Endoreg persists sensitive metadata in
`endoreg_db/models/metadata/sensitive_meta.py`; `lx-dtypes` provides the
export-oriented `SensitiveMeta` contract. Django foreign keys and relations do
not have the same shape as the flatter transfer model, so the adapter boundary
is intentional.

## Implemented Canonical Boundary

`endoreg_db.services.lx_video_contracts` is the canonical adapter for
`VideoFile`, `LabelVideoSegment`, `VideoState`, and `SensitiveMeta`. The
artificial-intelligence dataset export uses the same adapters.

- Django primary keys for sensitive metadata and segments are mapped
  deterministically to stable contract Universally Unique Identifiers (UUIDs).
- Segment states are normalized from persisted state; manual segments are not
  emitted as predictions.
- Missing labels, unresolvable label sets, missing states, unknown
  anonymization values, and contradictory dates fail loudly.
- Invalid segments are not silently skipped during export.
- The video contract references only the processed file path; the adapter does
  not export a raw-media reference.
- During JavaScript Object Notation (JSON) serialization, the dataset artifact
  path removes `PatientVideoFile.sensitive_meta`. Direct identifiers, date of
  birth, case number, external identifier, and raw text do not cross this
  boundary, and the strict export model rejects unknown top-level fields.

## Maintenance Rules

- Keep workflow behavior out of Django persistence models.
- Extend the shared `lx-dtypes` contract before adding new cross-repository
  fields.
- Resolve label-set identity explicitly and fail when it is ambiguous.
- Validate export structures at the boundary and preserve the prohibition on
  raw-media references.
- Use `feature-tracking/done/TypeSafety.yml`, not this document, for maturity
  and verification status.
