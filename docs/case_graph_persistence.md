# Case Graph Persistence

This document is the versioned relationship inventory for the
[`case_persistence`](../feature-tracking/CasePersistence.yml) feature. The YAML
tracker remains the only source of completion and approval status.

Inventory version: `case_graph_v1`.

## Ownership rules

`Case` owns the admission episode and references one or more
`PatientExamination` records through a many-to-many relation. An examination
owns its clinical observations and authored reports. File records own their
encrypted storage references and point to an examination; the case does not
duplicate file foreign keys. Consequently, a document is case-reachable only
through:

```text
Case -> PatientExamination -> RawPdfFile | VideoFile | PatientExaminationReport
```

The case API may attach an existing PDF or video only to an examination already
in that case. It must not move an artifact already assigned to another
examination, infer a patient, or mutate a closed case.

## Relationship inventory

| Entity | Persistent owner and direction | Cardinality | Time and provenance | Delete behavior | Case API representation |
| --- | --- | --- | --- | --- | --- |
| Patient | `Case.patient -> Patient` | one patient per case | Patient identity is independent of admission time | case cascades when patient is deleted | `patient` ID |
| Case | owns `case_id`, admission/leave timestamps and state | one stable UUID per episode | `created_at`, `updated_at`; admission and leave are timezone-aware | soft-delete flag exists; final retention is not yet approved | top-level case record |
| PatientExamination | `Case.patient_examinations` | one or more; an examination may currently be referenced by more than one case | examination date range and knowledge-base identity | case relation removal does not delete examination | nested `patient_examinations` |
| Examination indication | `PatientExaminationIndication.patient_examination -> PatientExamination` | many per examination | no independent event timestamp | cascades with examination | reachable through examination APIs |
| Patient finding | `PatientFinding.patient_examination -> PatientExamination` | many, including repeated finding types; each has a unique `instance_id` | created/updated and explicit deactivation actor/time | cascades with examination | reachable through finding/report APIs |
| Finding classification | `PatientFindingClassification.finding -> PatientFinding` | many per finding | active state plus validated descriptor snapshots | cascades with finding | finding payload |
| Finding intervention | `PatientFindingIntervention.finding -> PatientFinding` | many per finding | date and optional start/end timestamps | cascades with finding | finding payload |
| Authored text report | `PatientExaminationReport.patient_examination -> PatientExamination` | many versions/occurrences | created, updated, finalized, actors, template identity and snapshots | cascades with examination | `documents[]` as `text_report` |
| Raw PDF | `RawPdfFile.examination -> PatientExamination` | many per examination | creation/modification time, UUID, content hash and metadata | examination deletion sets the link to null; file lifecycle remains media-owned | `documents[]` as `pdf` |
| Video | `VideoFile.examination -> PatientExamination` | many per examination | upload/create/modify time, UUID, hashes, generation metadata | examination deletion sets the link to null; file lifecycle remains video-owned | `documents[]` as `video` |
| Video segment | `LabelVideoSegment.video_file -> VideoFile` | many per video | frame boundaries plus authoritative presentation-timestamp generation evidence | cascades with video | video segment APIs, not duplicated in case JSON |
| Frame/image | `Frame.video -> VideoFile` | many per video, unique frame number | frame number and presentation timestamp | cascades with video; extracted-file retention is media-owned | frame APIs |
| Frame annotation | annotation points to `Frame` | many per frame/annotator | information source and annotation workflow provenance | follows annotation/frame lifecycle | annotation APIs |
| DICOM export job/study | both point to `PatientExamination` | many jobs/studies per examination | source system, schema version, manifest hash and study date | protected references preserve imported identity | DICOM APIs |
| Medication | `Case.patient_medications` references patient-owned `PatientMedication` | many | current normalized row; no immutable case snapshot yet | relation removal does not delete medication | ID list |
| Medication schedule | `Case.patient_medication_schedules` references patient-owned schedule | many | created/updated timestamps; no immutable case snapshot yet | relation removal does not delete schedule | ID list |
| Lab sample | `Case.patient_lab_samples` references patient-owned sample | many | sample timestamp | relation removal does not delete sample | ID list |
| Lab value | `Case.patient_lab_values` references patient-owned value | many | measurement timestamp and range/unit fields | relation removal does not delete value | ID list |

## Mutation contract

`POST /api/cases/{case_id}/documents/` accepts the snake-case fields
`media_type`, `media_id`, and `patient_examination_id`.

- The request is validated once with an extra-forbid Pydantic schema.
- The case, examination, and media row are locked in one database transaction.
- The examination must already belong to the case and the same patient.
- Existing media patient, center, and sensitive-metadata patient references must
  agree with the case.
- An identical attachment is idempotent.
- Media linked to another examination is a conflict and is never moved.
- Closed or inactive cases reject attachment until an audited revision workflow
  exists.

The response is the canonical case representation, including every reachable
document occurrence.

## Query and lifecycle boundaries

### Repeated finding instances

The authoritative acceptance criteria are
`case_persistence/repeated_finding_instances` in
[`CasePersistence.yml`](../feature-tracking/CasePersistence.yml).
`PatientFinding.id` continues to identify existing database relationships.
`PatientFinding.instance_id` is a unique UUID identifying one lesion, not its
terminology type. The existing clinical ledger uses this same identity in
`PFinding.uuid` and its child references, so refreshing the ledger preserves it.
Report submissions create repeatable instances by supplying
`instance_id` alongside `finding_id`; updates may instead supply
`patient_finding_id`. When both identifiers are supplied they must agree.
Replaying the same UUID reuses the existing instance and its child relations.
An inactive instance or an instance owned by another examination or finding
type is rejected. The full findings list remains a replacement of the active
report findings, so callers must retain unchanged lesions in that list.

Legacy report submissions without instance identity remain supported only when
the finding type identifies at most one active instance. Repeated finding types
in one submission require explicit identities. The shared patient-findings POST
route also accepts `instance_id`: an identical retry returns the existing finding;
a changed create payload conflicts and requires PATCH. POST without a UUID
creates a new instance and does not promise replay detection.

The report segment selector accepts `patient_finding_id`. A report stores its
selected instance in the existing segment-selection map; the segment's existing
many-to-many relation can retain multiple lesions. Reads expose the linked
`attached_findings` and leave the singular selection empty when it is ambiguous;
report frame export requires an explicit selection in that case. Frame stepping
retains the stored finding selection. An explicit video examination takes
precedence over shared sensitive metadata. These operations serialize on the
owning examination before mutating findings or report selections.

Migration `0084_patient_finding_instances` backfills a distinct UUID for every
existing row without changing primary keys, child foreign keys, or segment
links. Apply the updated shared lx-dtypes contracts and application writers
together before enabling repeated-lesion entry; older writers may select by
type alone. The source checkout is required for local verification until these
shared changes are included in a released package. Downgrade refuses multiple
active instances of one type. A permitted downgrade removes UUIDs, so UUID-based
clients must be stopped before downgrading; no automatic merge is performed.

The current relationship is examination-local. Linking an index lesion to a
later scar, specimen, or histology outcome requires a separately validated
relationship in this same clinical graph; equal terminology names do not imply
that two findings represent the same lesion.

`CaseViewSet.get_queryset()` owns the read plan and prefetches examinations,
PDFs, videos, authored reports, medications, schedules, samples, and values.
`CaseSerializer` must not discover additional relationship families through
unbounded lazy traversal.

File bytes, encryption, streaming generations, frames, segments, and cleanup
remain owned by their media services and by
[`video_storage_normalization`](../feature-tracking/VideoStorageNormalization.yml).
Case persistence never exports raw media or deletes a file.

The following remain explicit gaps rather than implied behavior:

- immutable case snapshots or audited revisions for medication and laboratory
  state;
- unique ownership rules when one examination is referenced by multiple cases;
- image and DICOM summaries in the case response;
- retention, reconciliation, backup/restore, and concurrency-conflict
  operations.
